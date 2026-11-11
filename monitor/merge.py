# -*- coding: utf-8 -*-
"""合并去重 → 硬性条件 → 入选信号 → 自动收录或进观察名单。

读 raw/<渠道>/<日期>.json（最近 30 天）和 raw/x/<日期>.json，写：
  data/candidates.json   候选队列（按去重键）。status：收录 / 观察 / 候选 / 已在库 / 拒绝
  data/sources.json      每条线索的原始出处，按去重键归档，用于追溯
  data/games.json        过线的候选追加为新卡片（status=auto，等每周核验补全文字）

人工或每周会话可以直接改 candidates.json 里的 status、manual_signals、ai_tag、note，
这里不会覆盖这些字段；status 为"拒绝"的不会再被打开。
"""
import datetime
import glob
import os
import re

from . import net, util
from .collectors import threshold
from .x_ingest import classify_link

MANUAL_FIELDS = ('manual_signals', 'ai_tag_manual', 'note', 'reject_reason', 'name_manual')
TECH = re.compile(r'webgpu|three\.?js|webgl|babylon|playcanvas', re.I)
TECH_PLUS = re.compile(r'multiplayer|mmo\b|\b\d{3,}\s*players|procedural|openstreetmap|\bosm\b|real[- ]world|'
                       r'real earth|satellite|google maps|map data|gaussian|path[- ]?trac|ray[- ]?trac|多人|程序化|真实地图', re.I)
METRIC_LABEL = {
    'hn_points': 'Hacker News', 'threejs_views': 'three.js 论坛', 'github_stars': 'GitHub',
    'spawn_plays': 'Spawn 游玩数', 'x_views': 'X 帖子浏览', 'itch_rank': 'itch.io 网页新品榜',
}


def issue_of(date):
    """期号：所在周的周一日期。"""
    d = datetime.date.fromisoformat(date)
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def load_leads(date, days):
    """读最近 days 天各渠道的原始线索。"""
    leads = []
    for path in sorted(glob.glob(util.p('raw', '*', '*.json'))):
        ch = os.path.basename(os.path.dirname(path))
        d = os.path.basename(path)[:-5]
        if not re.match(r'\d{4}-\d\d-\d\d$', d) or d > date or util.days_between(d, date) > days:
            continue
        raw = util.load(path)
        if ch == 'x':
            for it in raw.get('items', []):
                if it['verdict'] != 'game':
                    continue
                for g in it.get('games', []):
                    pv = g.get('post_views') or (it.get('game') or {}).get('views_now') or it.get('views')
                    src = g.get('source_post') or it['link']
                    own = src == it['link']
                    leads.append({
                        'channel': 'x', 'date': d, 'title': g.get('name') or '', 'url': g['url'], 'key': g['key'],
                        'source_url': src, 'metrics': {'x_views': pv, 'x_likes': it.get('likes') if own else None},
                        'published': (g.get('post_time_utc') or it.get('publish_time_utc') or '')[:10],
                        'passes': True,
                        'ai_hint': (it.get('judgment') or {}).get('ai_evidence') or '',
                        'desc': (it.get('text') or '')[:400] if own else '',
                        'x': {'handle': it['handle'] if own else (re.search(r'x\.com/([^/]+)', src) or [0, ''])[1],
                              'found_how': g.get('how'), 'judged_by': (it.get('judgment') or {}).get('by') or '规则',
                              'other_links': (it.get('game') or {}).get('other_links', []),
                              'via_post': it['link']},
                    })
        else:
            for l in raw.get('leads', []):
                if l.get('key'):
                    leads.append(dict(l, date=d))
    return leads


def squash(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def match_library(key, url, name, lib):
    """和收录库比对。返回 (状态, 说明)：已在库 / 疑似仿冒 / None。"""
    host = util.host_of(url)
    for g in lib:
        if key == g.get('key') or key in g.get('alt_keys', []):
            return '已在库', g['id']
        if '/' not in g.get('key', '/') and host == util.host_of(g['url']):
            return '已在库', g['id']
    if '/' not in key:                               # 独立域名才查仿冒
        label = squash(host.rsplit('.', 1)[0])
        for g in lib:
            if '/' in g.get('key', '/'):
                continue
            ghost = util.host_of(g['url'])
            gl = squash(util.root_domain(ghost).rsplit('.', 1)[0])
            if len(gl) >= 7 and gl in label and util.root_domain(host) != util.root_domain(ghost):
                return '疑似仿冒', g['id']
    return None, None


def signals(c, cfg, date):
    """入选信号。每类最多算一条，返回 [(类别, 说明)]。"""
    out = {}
    recent = [l for l in c['leads']]
    xv = max((l['metrics'].get('x_views') or 0 for l in recent if l['channel'] == 'x'), default=0)
    hn_recent = max((l['metrics'].get('hn_points') or 0 for l in recent if l['channel'] == 'hn'
                     and util.days_between(l.get('published') or l['date'], date) <= 7 + 3), default=0)
    boosted = threshold(cfg, 'x_views_signal', date) < cfg['thresholds']['x_views_signal']
    tail = '（新模型发布后两周内，门槛减半）' if boosted else ''
    if xv >= threshold(cfg, 'x_views_signal', date):
        out['传播'] = f'X 单帖浏览 {util.fmt_num(xv)}' + (tail if xv < cfg['thresholds']['x_views_signal'] else '')
    elif hn_recent >= threshold(cfg, 'hn_points_7d', date):
        out['传播'] = f'Hacker News {hn_recent} 分' + (tail if hn_recent < cfg['thresholds']['hn_points_7d'] else '')
    plays = max((int(l['metrics'].get('spawn_plays') or 0) for l in recent), default=0)
    if plays >= cfg['thresholds']['players_signal']:
        out['规模'] = f'平台显示游玩 {util.fmt_num(plays)} 次'
    text = ' '.join((l.get('title') or '') + ' ' + (l.get('desc') or '') for l in recent)
    if TECH.search(text) and TECH_PLUS.search(text):
        out['技术'] = '线索原文提到：' + '、'.join(dict.fromkeys(
            m.group(0).lower() for m in list(TECH.finditer(text))[:2] + list(TECH_PLUS.finditer(text))[:2]))
    if any('store.steampowered.com' in u for l in recent for u in (l.get('x') or {}).get('other_links', [])):
        out['商业'] = '同时有 Steam 页面'
    for s in c.get('manual_signals', []):            # 人工或每周核验补充的信号，如 {"类别":"玩法","说明":"…"}
        out[s['类别']] = s['说明']
    return [{'类别': k, '说明': v} for k, v in out.items()]


def ai_tag_of(c, cfg):
    if c.get('ai_tag_manual'):
        return c['ai_tag_manual'], c.get('ai_evidence', '')
    host = util.host_of(c['url'])
    for l in c['leads']:
        if l.get('ai_hint'):
            who = {'x': '来源帖子', 'hn': 'Hacker News 标题', 'github': '仓库描述', 'threejs': '论坛帖子',
                   'awesome': '', 'spawn': '', 'itch': 'itch.io 描述'}.get(l['channel'], '')
            return 'ai', (who + '：' if who else '') + l['ai_hint']
    if any(host == h or host.endswith('.' + h) for h in cfg['game_hosts']['ai_platforms']):
        return 'ai', f'发布在 AI 创作平台（{host}）'
    return 'unlabeled', ''


def is_ai_platform(url, cfg):
    host = util.host_of(url)
    return any(host == h or host.endswith('.' + h) for h in cfg['game_hosts']['ai_platforms'])


def page_meta(url):
    """读游戏页面的标题、描述、封面图地址。只取 <head> 里的声明，原样保留。"""
    h = net.get(url, timeout=20, maxb=3_000_000)
    if not h:
        return {'ok': False}
    def m(p):
        x = re.search(p, h, re.S | re.I)
        import html as _h
        return _h.unescape(re.sub(r'\s+', ' ', x.group(1)).strip()) if x else ''
    def meta(name):
        return (m(rf'<meta[^>]+(?:property|name)=["\']{name}["\'][^>]+content=["\']([^"\']*)') or
                m(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{name}["\']'))
    from urllib.parse import urljoin
    img = meta('og:image') or meta('twitter:image')
    icon = m(r'<link[^>]+rel=["\'](?:apple-touch-icon|icon|shortcut icon)["\'][^>]+href=["\']([^"\']+)')
    return {'ok': True, 'title': m(r'<title[^>]*>(.*?)</title>')[:120],
            'desc': (meta('og:description') or meta('description'))[:400],
            'image': urljoin(url, img) if img else '', 'icon': urljoin(url, icon) if icon else '',
            'has_canvas': bool(re.search(r'<canvas|three(\.module)?(\.min)?\.js|webgl|webgpu|phaser|pixi|babylon|unity', h, re.I))}


def fetch_image(url, gid, kind):
    if not url:
        return None
    b = net.get(url, binary=True, maxb=1_500_000, timeout=25)
    if len(b) < 800:
        return None
    ext = 'png' if b[:8] == b'\x89PNG\r\n\x1a\n' else 'jpg' if b[:2] == b'\xff\xd8' else \
        'webp' if b[8:12] == b'WEBP' else 'gif' if b[:3] == b'GIF' else \
        'svg' if b.lstrip()[:5] in (b'<?xml', b'<svg ') else 'ico' if b[:4] == b'\x00\x00\x01\x00' else None
    if not ext:
        return None
    name = f'{gid}_{kind}.{ext}'
    os.makedirs(util.p('assets', 'games'), exist_ok=True)
    with open(util.p('assets', 'games', name), 'wb') as f:
        f.write(b)
    return name


def make_card(c, cfg, date, lib_ids):
    """把过线的候选生成一张自动卡片：只放能追溯到出处的原文和数字，不写主观描述。"""
    meta = c.get('page') or page_meta(c['url'])
    name = c.get('name_manual') or c['name'] or meta.get('title') or c['key']
    gid = base = util.slug(name) or util.slug(c['key'])
    n = 2
    while gid in lib_ids:
        gid = f'{base}-{n}'
        n += 1
    stats, src_bits = [], []
    best = {}
    for l in c['leads']:
        for k, v in l['metrics'].items():
            if v is None or k not in METRIC_LABEL:
                continue
            if k not in best or (k != 'itch_rank' and int(v) > int(best[k][0])):
                best[k] = (v, l)
    for k, (v, l) in best.items():
        if k == 'hn_points':
            val = f"{v} 分 / {l['metrics'].get('hn_comments') or 0} 评论"
        elif k == 'threejs_views':
            val = f'{util.fmt_num(v)} 次浏览'
        elif k == 'github_stars':
            val = f"{util.fmt_num(v)} 星" + (f" / {util.fmt_num(l['metrics']['github_forks'])} 分叉" if l['metrics'].get('github_forks') else '')
        elif k == 'itch_rank':
            val = f'第 {v} 名'
        else:
            val = util.fmt_num(v) + (' 次' if k == 'spawn_plays' else '')
        stats.append({'label': METRIC_LABEL[k], 'value': val, 'doubt': False})
    chans = {'x': 'X 帖子数据读取自公开接口', 'hn': 'Hacker News 官方接口', 'threejs': 'three.js 官方论坛接口',
             'github': 'GitHub 接口', 'itch': 'itch.io 榜单', 'awesome': 'AI 游戏精选清单', 'spawn': 'Spawn 平台页面显示值'}
    for l in c['leads']:
        b = chans.get(l['channel'])
        if b and b not in src_bits:
            src_bits.append(b)
    quote_src, quote = '', ''
    if meta.get('desc'):
        quote_src, quote = '页面自述', meta['desc']
    else:
        for l in c['leads']:
            if l.get('desc'):
                quote_src, quote = {'x': '来源帖子原文', 'github': '仓库描述'}.get(l['channel'], '渠道描述'), l['desc']
                break
    pub = c.get('published') or ''
    card = {
        'id': gid, 'name': name, 'kind': '', 'group': '待归类', 'group_type': 'pending',
        'url': c['url'], 'key': c['key'],
        'date_text': f'{pub} 首次公开' if pub else '首次公开日期未确认',
        'stats': stats, 'play': '', 'quote': quote, 'quote_src': quote_src, 'tech': '',
        'xcheck': '', 'src': '；'.join(src_bits) + f'（数据截至 {date}）', 'hot': False,
        'ai_tag': c['ai_tag'], 'ai_evidence': c['ai_evidence'],
        'status': 'auto', 'first_seen': c['first_seen'], 'added': date, 'issue': issue_of(date),
        'signals': c['signals'],
        'icon': fetch_image(meta.get('icon'), gid, 'icon'),
        'shot': fetch_image(meta.get('image') or next((l.get('image') for l in c['leads'] if l.get('image')), ''), gid, 'shot'),
        'extra_shots': [],
        'sources': [{'channel': l['channel'], 'url': l['source_url'], 'date': l['date'], 'metrics': l['metrics']} for l in c['leads']],
        'x_handles': sorted({l['x']['handle'] for l in c['leads'] if l.get('x') and l['x'].get('handle')}),
        'source_posts': sorted({l['source_url'] for l in c['leads'] if l['channel'] == 'x'}),
    }
    xv = max((l['metrics'].get('x_views') or 0 for l in c['leads']), default=0)
    card['hot'] = xv >= cfg['thresholds']['x_views_hot']
    return card


def run(date=None, publish=True):
    cfg = util.config()
    date = date or util.today()
    lib = util.load(util.p('data', 'games.json'), [])
    cands = util.load(util.p('data', 'candidates.json'), {})
    leads = load_leads(date, cfg['window']['new_days'])

    for l in leads:
        if l['key'] not in cands and not l.get('passes'):
            continue                                 # 没过渠道门槛的线索不新建候选，只留在 raw/ 里
        c = cands.setdefault(l['key'], {'key': l['key'], 'url': l['url'], 'name': '', 'leads': [],
                                        'first_seen': {'date': l['date'], 'channel': l['channel']}, 'status': '候选'})
        sig = (l['channel'], l['source_url'])
        old = next((x for x in c['leads'] if (x['channel'], x['source_url']) == sig), None)
        slim = {k: l.get(k) for k in ('channel', 'date', 'title', 'source_url', 'metrics', 'published', 'passes',
                                      'ai_hint', 'desc', 'x', 'image') if l.get(k) not in (None, '')}
        slim.setdefault('metrics', {})
        if old:
            first = old['date']
            old.update(slim)                         # 同一出处：数字更新为最新一次读到的
            old['date'], old['last_date'] = first, l['date']
        else:
            c['leads'].append(slim)
        if l['date'] < c['first_seen']['date']:
            c['first_seen'] = {'date': l['date'], 'channel': l['channel']}

    lib_ids = {g['id'] for g in lib}
    new_cards = []
    for key, c in cands.items():
        if c['status'] in ('拒绝', '收录'):
            continue
        c['name'] = c.get('name_manual') or next((l['title'] for l in c['leads'] if l.get('title')
                                                   and l['channel'] in ('x', 'spawn', 'awesome', 'itch')), '') \
            or next((l['title'] for l in c['leads'] if l.get('title')), '')
        c['name'] = re.sub(r'^(Show|Launch) HN:\s*', '', c['name'])
        pubs = sorted(l['published'] for l in c['leads'] if l.get('published'))
        c['published'] = pubs[0] if pubs else ''
        st, gid = match_library(key, c['url'], c['name'], lib)
        if st == '已在库':
            c.update(status='已在库', library_id=gid, reasons=[f'试玩链接和已收录的 {gid} 相同'])
            continue
        if st == '疑似仿冒':
            c.update(status='拒绝', library_id=gid, reject_reason=f'域名包含已收录游戏 {gid} 的名字，疑似仿冒或镜像站')
            continue
        kind = classify_link(c['url'], cfg)
        hard, why = {}, []
        hard['可玩链接'] = kind in ('game', 'site')
        if not hard['可玩链接']:
            why.append({'repo': '只有代码仓库链接，没有试玩页面', 'article': '链接是文章页，不是试玩页面'}.get(kind, '链接不是试玩页面'))
        age = util.days_between(c['published'], date) if c['published'] else None
        reheat = any(l.get('passes') and util.days_between(l['date'], date) <= cfg['window']['reheat_days']
                     and l['channel'] in ('x', 'hn') for l in c['leads'])
        hard['近期'] = (age is not None and age <= cfg['window']['new_days']) or reheat
        if not hard['近期']:
            why.append('首次公开超过 30 天，且近 7 天没有新的热度' if age is not None else '首次公开日期未知')
        c['ai_tag'], c['ai_evidence'] = ai_tag_of(c, cfg)
        c['signals'] = signals(c, cfg, date)
        c['hard'] = hard
        # 有 AI 证据的只要 1 条信号，没有 AI 证据的要 2 条（门槛在 config.json 里改）
        need = cfg['thresholds']['signals_to_publish_ai' if c['ai_tag'] == 'ai' else 'signals_to_publish']
        if all(hard.values()) and c['signals'] and not is_ai_platform(c['url'], cfg):
            # 独立站点再看一眼页面：没有任何可玩内容的特征（画布、游戏引擎脚本）就不当作游戏
            if 'page' not in c:
                c['page'] = page_meta(c['url'])
            hard['页面可玩特征'] = bool(c['page'].get('has_canvas'))
            if not hard['页面可玩特征']:
                why.append('页面里没有发现画布或游戏引擎脚本，可能不是游戏')
        if all(hard.values()) and len(c['signals']) >= need:
            c['status'] = '收录'
            c['reasons'] = [f"{s['类别']}：{s['说明']}" for s in c['signals']]
            if publish:
                card = make_card(c, cfg, date, lib_ids)
                lib.append(card)
                lib_ids.add(card['id'])
                c['game_id'] = card['id']
                new_cards.append(card)
        elif all(hard.values()) and c['signals']:
            c['status'] = '观察'
            c['reasons'] = [f"{s['类别']}：{s['说明']}" for s in c['signals']] + [f'入选信号不足 {need} 条']
        else:
            c['status'] = '候选'
            c['reasons'] = why or ['暂无入选信号']
        c['updated'] = date

    util.save(util.p('data', 'candidates.json'), cands)
    if new_cards:
        util.save(util.p('data', 'games.json'), lib)
    src = {}
    for key, c in cands.items():
        src[key] = {'name': c['name'], 'status': c['status'], 'game_id': c.get('game_id') or c.get('library_id'),
                    'leads': [{'channel': l['channel'], 'source_url': l['source_url'], 'first_date': l['date'],
                               'metrics': l['metrics']} for l in c['leads']]}
    util.save(util.p('data', 'sources.json'), src)
    cnt = {}
    for c in cands.values():
        cnt[c['status']] = cnt.get(c['status'], 0) + 1
    print(f'[merge] {date} 线索 {len(leads)} → 候选 {len(cands)}：{cnt}；本次新收录 {[c["name"] for c in new_cards]}')
    return {'date': date, 'counts': cnt, 'new': [c['id'] for c in new_cards]}


if __name__ == '__main__':
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None)
