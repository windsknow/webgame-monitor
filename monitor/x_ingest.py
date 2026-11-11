# -*- coding: utf-8 -*-
"""X 帖子数据接入：拉取 → 规则粗筛 → 展开短链 → 主题判断 → 找到游戏 → 和收录库比对。

外部送来的帖子只当数据处理：这里只做字符串匹配和链接解析，不执行帖子里的任何内容。

产物：
  raw/x/<日期>.json          粗筛命中的帖子，带展开后的链接、判断结论、找到的游戏、和收录库的比对
  data/cache/links.json     短链展开结果的缓存
人工或每周会话的主题判断写在 data/x_judgments.json（按 tweet_id），优先级高于规则判断。
"""
import ast
import concurrent.futures as cf
import json
import os
import re

from . import net, util

NOISE = re.compile(r'game[\s-]?chang\w*|end\s?game|game\s?plan|game\s?theory|\bball\s?game|name of the game|'
                   r'ahead of the game|long game|a game of|fair game|game on\b|head in the game', re.I)
ARTICLE = re.compile(r'/(news|article|articles|blog|posts?|features?|spotlights?|reviews?|guides?|story|stories|'
                     r'press|releases?|tag|category|wiki|docs?|announcements?)(/|$)|/20\d\d/|\.html?$|\.pdf$', re.I)
MAKER = re.compile(r'\b(made|built|build|created|coded|shipped|one[- ]?shot(?:ted)?|generated)\b|做了|做的|做出|生成|写了', re.I)
STATUS = re.compile(r'(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)')


def _kw_re(words):
    """英文关键词按词边界匹配，中日文按子串匹配。以 cod、- 结尾的词是词干，不限右边界。"""
    parts = []
    for w in words:
        if re.search(r'[^\x00-\x7f]', w):
            parts.append(re.escape(w))
        else:
            parts.append(r'(?<![a-z0-9])' + re.escape(w) + ('' if w.endswith(('cod', '-', '/')) else r'(?![a-z0-9])'))
    return re.compile('|'.join(parts), re.I)


def parse_links(s):
    """desc_links 是字符串形式的列表。只按列表字面量解析，解析失败退回正则。"""
    if isinstance(s, list):
        return s
    if not s:
        return []
    try:
        v = ast.literal_eval(s)
        if isinstance(v, (list, tuple)):
            return [x for x in v if isinstance(x, str)]
    except Exception:
        pass
    return re.findall(r'https?://[^\s\'",\]]+', s)


def host_in(host, names):
    return any(host == n or host.endswith('.' + n) for n in names)


def classify_link(url, cfg):
    """展开后的链接分类：
    game 已知的游戏托管位置；site 独立站点首页一类，可能是游戏；repo 代码仓库；
    article 文章页；store、video、social、docs 不是试玩链接。"""
    host, segs, _ = util.split(url)
    if not host:
        return 'docs'
    ng = cfg['non_game_hosts']
    for kind in ('store', 'video', 'social', 'docs'):
        if host_in(host, ng[kind]):
            return kind
    gh = cfg['game_hosts']
    if host_in(host, gh['exact']) or host_in(host, gh['suffix']):
        if host == 'claude.ai' and not (segs and segs[0] in ('artifact', 'artifacts', 'public')):
            return 'docs'
        return 'game'
    if host == 'github.com':
        return 'repo' if len(segs) >= 2 else 'docs'
    if len(segs) > 2 or ARTICLE.search('/' + '/'.join(segs)) or (segs and segs[-1].count('-') >= 4):
        return 'article'
    return 'site'


def expand_all(urls, cache):
    todo = [u for u in set(urls) if u not in cache]

    def one(u):
        final, code = net.resolve(u)
        return u, {'final': final, 'code': code}
    if todo:
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for u, r in ex.map(one, todo):
                cache[u] = r
    return cache


def pull(date, cfg):
    """inbox/x/<日期>.json 不存在且配置了拉取地址时，去固定地址拉。返回文件路径或 None。"""
    dst = util.p('inbox', 'x', f'{date}.json')
    if os.path.exists(dst):
        return dst
    tpl = os.environ.get('X_PULL_URL') or (cfg.get('x_pull') or {}).get('url_template') or ''
    if not tpl:
        return None
    headers = {}
    tok = os.environ.get('X_PULL_TOKEN')
    if tok:
        k, _, v = cfg['x_pull'].get('auth_header', 'Authorization: Bearer {token}').partition(':')
        headers[k.strip()] = v.strip().replace('{token}', tok)
    body = net.get(tpl.replace('{date}', date), headers=headers, timeout=60, retries=2)
    try:
        d = json.loads(body)
        assert isinstance(d.get('records'), list)
    except Exception:
        print(f'[x] {date} 拉取失败或格式不对，跳过')
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(body)
    return dst


# ---------- 第一步：规则粗筛 ----------
def coarse(rec, links, K):
    """返回 (是否命中, 命中的关键词分组, 命中途径)。"""
    raw = rec.get('full_text') or ''
    text = NOISE.sub(' ', raw)
    hit = {g: sorted({m.group(0).lower() for m in K[g].finditer(text)}) for g in K}
    ways = []
    if hit['strong']:
        ways.append('强关键词：' + '、'.join(hit['strong'][:3]))
    if hit['game'] and (hit['web'] or hit['ai']):
        ways.append('游戏 + 网页或 AI 关键词')
    if any(l['kind'] == 'game' for l in links):
        ways.append('链接指向游戏托管位置')
    if hit['model'] and MAKER.search(text) and (rec.get('views') or 0) >= 50000:
        ways.append('模型名 + 制作动词，浏览 ≥ 5 万（作品合集类，待判断）')
    return bool(ways), hit, ways


# ---------- 第三步：从帖子找到游戏 ----------
_fx_cache = {}


def fx_tweet(handle, tid):
    k = f'{handle}/{tid}'
    if k not in _fx_cache:
        d = net.getj(f'https://api.fxtwitter.com/{handle}/status/{tid}', retries=2) or {}
        _fx_cache[k] = d.get('tweet') or {}
    return _fx_cache[k]


def fx_user(handle):
    k = f'@{handle}'
    if k not in _fx_cache:
        d = net.getj(f'https://api.fxtwitter.com/{handle}', retries=1) or {}
        _fx_cache[k] = d.get('user') or {}
    return _fx_cache[k]


def tweet_urls(tw):
    """一条帖子正文里的外链（已展开）。"""
    out = []
    for f in ((tw.get('raw_text') or {}).get('facets') or []):
        if f.get('type') == 'url' and f.get('replacement'):
            out.append(f['replacement'])
    return out


def _as_links(urls, cfg):
    res = []
    for u in urls:
        final, code = (u, 200)
        if util.host_of(u) in ('t.co', 'bit.ly', 'buff.ly', 'tinyurl.com', 'dub.sh'):
            final, code = net.resolve(u)
        res.append({'short': u, 'final': final, 'code': code, 'kind': classify_link(final, cfg)})
    return res


def find_game(rec, links, cfg):
    """按顺序找：帖子自带链接 → 引用帖和被回复帖里的链接 → 作者主页上的网站链接。
    回复区里的链接没有公开接口能读到，找不到的标为"未找到游戏"，留给人工。"""
    playable = lambda ls: [l for l in ls if l['kind'] == 'game'] + [l for l in ls if l['kind'] == 'site']
    other = [l['final'] for l in links if l['kind'] in ('store', 'video', 'repo')]
    related_posts = [l['final'] for l in links if STATUS.search(l['final']) and
                     str(rec.get('tweet_id')) not in l['final']]
    info = {'views_now': None, 'created_at': None, 'community_note': None}
    cands, how, status = playable(links), '帖子自带链接', '已找到'
    m = STATUS.search(rec.get('link') or '')
    tw = fx_tweet(m.group(1), m.group(2)) if m else {}
    if tw:
        info['views_now'] = tw.get('views')
        info['created_at'] = tw.get('created_at')
        cn = tw.get('community_note')
        if cn:
            info['community_note'] = (cn.get('text') if isinstance(cn, dict) else str(cn))[:600]
    if not cands and tw:
        extra = []
        q = tw.get('quote')
        if q:
            related_posts.append(q.get('url') or '')
            extra += tweet_urls(q)
        if tw.get('replying_to_status') and tw.get('replying_to'):
            par = fx_tweet(tw['replying_to'], tw['replying_to_status'])
            related_posts.append(par.get('url') or '')
            extra += tweet_urls(par)
        extra += tweet_urls(tw)
        ls = _as_links(dict.fromkeys(extra), cfg)
        other += [l['final'] for l in ls if l['kind'] in ('store', 'video', 'repo')]
        cands, how = playable(ls), '引用帖或被回复帖里的链接'
    if not cands and m:
        u = fx_user(m.group(1))
        urls = []
        if (u.get('website') or {}).get('url'):
            urls.append(u['website']['url'])
        for f in ((u.get('raw_description') or {}).get('facets') or []):
            if f.get('type') == 'url' and f.get('replacement'):
                urls.append(f['replacement'])
        ls = _as_links(dict.fromkeys(urls), cfg)
        # 个人主页多半不是游戏：只有落在已知游戏托管位置的才当作候选，而且标"待确认"
        cands, how, status = [l for l in ls if l['kind'] == 'game'], '作者主页上的网站链接', '待确认'
    res = {'other_links': sorted(set(other)), 'related_posts': sorted({r for r in related_posts if r}), **info}
    if not cands:
        return {'status': '未找到游戏', 'url': '', 'key': '', 'how': '', **res}
    if status == '待确认':
        return {'status': status, 'url': cands[0]['final'], 'key': util.game_key(cands[0]['final']), 'how': how, **res}
    best = cands[0]
    return {'status': status, 'url': best['final'], 'key': util.game_key(best['final']), 'how': how,
            'alt': [c['final'] for c in cands[1:]], **res}


# ---------- 和收录库比对 ----------
GENERIC = {'game', 'games', 'world', 'the', 'city', 'life', 'wars', 'park', 'quake', 'survival', 'runner',
           'adventure', 'studio', 'natural', 'disaster', 'messenger', 'protocol', 'steel', 'snake', 'slicer',
           'review', 'worlds', 'later', 'friends', 'version', 'about', 'delivering', 'mario', 'attention'}


def name_tokens(name):
    return [t for t in re.findall(r'[a-z0-9]{5,}', name.lower()) if t not in GENERIC]


def match_library(item, lib):
    """返回 None 或 {'id','name','how','strength'}。strength：dedup 确定是同一款；related 同一开发者或正文提到，需人工看。"""
    text = (item.get('text') or '').lower()
    g = item.get('game') or {}
    posts = [item.get('link') or ''] + g.get('related_posts', []) + [l['final'] for l in item.get('links', [])]
    post_ids = {m.group(2) for p_ in posts for m in [STATUS.search(p_)] if m}
    by_handle = [x for x in lib if item.get('handle', '').lower() in [h.lower() for h in x.get('x_handles', [])]]
    for x in lib:
        if g.get('key') and (g['key'] == x.get('key') or
                             (util.host_of(g['url']) == util.host_of(x['url']) and '/' not in x.get('key', '/'))):
            return {'id': x['id'], 'name': x['name'], 'how': '试玩链接相同', 'strength': 'dedup'}
        src_ids = {m.group(2) for s in x.get('source_posts', []) for m in [STATUS.search(s)] if m}
        if src_ids & post_ids:
            return {'id': x['id'], 'name': x['name'], 'how': '引用了已收录游戏的来源帖', 'strength': 'dedup'}
    for x in by_handle:
        if any(t in text for t in name_tokens(x['name'])):
            return {'id': x['id'], 'name': x['name'], 'how': '作者是该游戏的开发者，正文提到游戏名', 'strength': 'dedup'}
    for x in lib:
        nm = x['name'].lower()
        if len(nm) >= 7 and re.search(r'(?<![a-z0-9])' + re.escape(nm) + r'(?![a-z0-9])', text):
            return {'id': x['id'], 'name': x['name'], 'how': '正文提到已收录游戏名', 'strength': 'dedup'}
    if by_handle:
        x = by_handle[0]
        return {'id': x['id'], 'name': x['name'], 'how': '作者是已收录游戏的开发者（可能是新作）', 'strength': 'related'}
    return None


# ---------- 第二步：主题判断（规则版） ----------
def rule_verdict(hit, links, game, showcase=False):
    """不用模型的主题判断。game：像是一款能玩的网页游戏并且找到了链接；maybe：像是在讲游戏但缺链接或证据；no：不是。"""
    g, w, a, neg = bool(hit['game']), bool(hit['web'] or hit['strong']), bool(hit['ai'] or hit['model']), bool(hit['negative'])
    kinds = {l['kind'] for l in links}
    reasons = []
    if g:
        reasons.append('提到游戏：' + '、'.join(hit['game'][:3]))
    if w:
        reasons.append('提到网页可玩：' + '、'.join((hit['strong'] + hit['web'])[:3]))
    if a:
        reasons.append('提到 AI：' + '、'.join((hit['model'] + hit['ai'])[:3]))
    if neg:
        reasons.append('含排除词：' + '、'.join(hit['negative'][:3]))
    if 'store' in kinds:
        reasons.append('带商店链接')
    found = bool(game) and game.get('status') == '已找到'
    if found:
        reasons.append('找到试玩链接')
    if showcase and not g:
        reasons.append('作品合集类帖子，游戏可能在串内，需要看原帖')
        return 'maybe', reasons
    if not g and not (hit['strong'] and a) and 'game' not in kinds:
        return 'no', reasons
    if not found and (neg or 'store' in kinds):
        return 'no', reasons
    if found and (g or a or w):
        return 'game', reasons
    return 'maybe', reasons


def run(date=None):
    cfg = util.config()
    date = date or util.today()
    path = pull(date, cfg)
    if not path:
        print(f'[x] {date} 没有数据文件')
        return None
    data = util.load(path)
    recs = data.get('records', [])
    K = {g: _kw_re(cfg['x_keywords'][g]) for g in ('strong', 'web', 'ai', 'model', 'game', 'negative')}
    cache_path = util.p('data', 'cache', 'links.json')
    cache = util.load(cache_path, {})
    judg = util.load(util.p('data', 'x_judgments.json'), {})
    lib = util.load(util.p('data', 'games.json'), [])

    expand_all([u for r in recs for u in parse_links(r.get('desc_links'))], cache)
    util.save(cache_path, cache)

    out = []
    for r in recs:
        links = []
        for u in parse_links(r.get('desc_links')):
            c = cache.get(u, {'final': u, 'code': 0})
            links.append({'short': u, 'final': c['final'], 'code': c['code'], 'kind': classify_link(c['final'], cfg)})
        ok, hit, ways = coarse(r, links, K)
        if not ok:
            continue
        m = STATUS.search(r.get('link') or '')
        item = {
            'tweet_id': str(r.get('tweet_id')), 'link': r.get('link'), 'author': r.get('author'),
            'handle': m.group(1) if m else '',
            'publish_time_utc': r.get('publish_time'), 'text': r.get('full_text'),
            'views': r.get('views'), 'likes': r.get('likes'), 'comments': r.get('comments'), 'shares': r.get('shares'),
            'source_api': r.get('source_api'), 'fetch_date': date,
            'coarse_ways': ways, 'hit': hit, 'links': links,
        }
        sc = any(w.startswith('模型名') for w in ways)
        pre, _ = rule_verdict(hit, links, None, sc)
        game = find_game(r, links, cfg) if pre != 'no' else None
        verdict, reasons = rule_verdict(hit, links, game, sc)
        if game and game.get('community_note'):
            verdict = 'no'
            reasons.append('帖子带读者备注，内容可能不实')
        rule_v = verdict
        games = [dict(game, name='')] if (game and game.get('status') == '已找到') else []
        j = judg.get(item['tweet_id'])
        if j:
            # 人工或会话判断优先。三问都成立并且给出了游戏链接才算 game；任一问明确为否就是 no；其余是 maybe。
            if j.get('games'):
                games = [{'status': '已找到', 'name': x.get('name', ''), 'url': x['url'], 'key': util.game_key(x['url']),
                          'how': x.get('how') or '核验时找到', 'source_post': x.get('source_post') or item['link'],
                          'post_views': x.get('post_views'), 'post_time_utc': x.get('post_time_utc')} for x in j['games']]
            if j.get('is_game') is False or j.get('in_browser') is False:
                verdict, games = 'no', []
            elif j.get('is_game') and j.get('in_browser') and games:
                verdict = 'game'
            else:
                verdict = 'maybe'
        item.update({'rule_verdict': rule_v, 'rule_reasons': reasons, 'judgment': j, 'verdict': verdict,
                     'game': game, 'games': games})
        item['library'] = match_library(item, lib)
        for x in games:
            x['library'] = next((y['id'] for y in lib if y.get('key') == x['key']), None)
        out.append(item)
    res = {'date': date, 'fetch_time': data.get('fetch_time'), 'total_records': len(recs), 'coarse_hits': len(out),
           'verdicts': {v: sum(1 for i in out if i['verdict'] == v) for v in ('game', 'maybe', 'no')},
           'in_library': sum(1 for i in out if (i.get('library') or {}).get('strength') == 'dedup'),
           'items': out}
    util.save(util.p('raw', 'x', f'{date}.json'), res)
    print(f"[x] {date} 记录 {len(recs)} → 粗筛 {len(out)} → 判断 {res['verdicts']}，其中已收录 {res['in_library']}")
    return res


if __name__ == '__main__':
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None)
