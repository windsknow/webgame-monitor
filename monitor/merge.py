# -*- coding: utf-8 -*-
"""合并去重 → 判断是不是网页游戏 → 入库 → 标重点。

读 raw/<渠道>/<日期>.json（最近 30 天）和 raw/x/<日期>.json，写：
  data/candidates.json   线索队列（按去重键）。status：收录 / 观察 / 候选 / 已在库 / 拒绝
  data/sources.json      每条线索的原始出处，按去重键归档，用于追溯
  data/games.json        入库的游戏。自动入库的卡片 status=auto，等每周核验补全文字

2026-11-12 起的规则：过了渠道入库线、判断为网页游戏、并且是近期的，直接入库，
不要求传播数据、游玩次数或试玩链接。达到重点线的标为重点（hot）。

人工或每周会话可以直接改 candidates.json 里的 status、manual_signals、ai_tag_manual、note、name_manual、is_game_manual，
这里不会覆盖这些字段；status 为"拒绝"的不会再被打开，对应的自动卡片会被撤下。
"""
import datetime
import glob
import os
import re

from . import net, util, x_ingest
from .collectors import ai_hint
from .x_ingest import STATUS, classify_link, host_in

TECH = re.compile(r'webgpu|three\.?js|webgl|babylon|playcanvas', re.I)
TECH_PLUS = re.compile(r'multiplayer|mmo\b|\b\d{3,}\s*players|procedural|openstreetmap|\bosm\b|real[- ]world|'
                       r'real earth|satellite|google maps|map data|gaussian|path[- ]?trac|ray[- ]?trac|多人|程序化|真实地图', re.I)
CHARTS = ('spawn', 'verse8', 'astrocade', 'rosebud', 'wavedash')
# 渠道读到的数字 → 卡片上的标签。第三项为 True 的是口径存疑的数字（页面标红，不参与重点判断）
METRIC_LABEL = {
    'x_views': ('X 帖子浏览', False), 'hn_points': ('Hacker News', False), 'threejs_views': ('three.js 论坛', False),
    'github_stars': ('GitHub', False), 'itch_rank': ('itch.io 网页新品榜', False),
    'spawn_plays': ('Spawn 游玩数', False), 'astrocade_plays': ('Astrocade 游玩数', False),
    'rosebud_plays': ('Rosebud 游玩数', False), 'verse8_sessions': ('Verse8 游玩数', True),
    'wavedash_playing': ('Wavedash 实时在线', False),
}
PLAYS = ('spawn_plays', 'astrocade_plays', 'rosebud_plays')
CH_NAME = {'x': 'X 帖子', 'hn': 'Hacker News', 'threejs': 'three.js 论坛', 'github': 'GitHub', 'itch': 'itch.io 网页新品榜',
           'awesome': 'AI 游戏精选清单', 'spawn': 'Spawn 热榜', 'verse8': 'Verse8 热门', 'astrocade': 'Astrocade 热门页',
           'rosebud': 'Rosebud 首页精选', 'wavedash': 'Wavedash 热门'}
CH_SRC = {'x': 'X 帖子数据读取自公开接口', 'hn': 'Hacker News 官方接口', 'threejs': 'three.js 官方论坛接口',
          'github': 'GitHub 接口', 'itch': 'itch.io 榜单', 'awesome': 'AI 游戏精选清单', 'spawn': 'Spawn 平台页面显示值',
          'verse8': 'Verse8 平台页面显示值', 'astrocade': 'Astrocade 平台页面显示值', 'rosebud': 'Rosebud 平台页面显示值',
          'wavedash': 'Wavedash 平台页面显示值'}
LEAD_KEYS = ('channel', 'date', 'title', 'source_url', 'metrics', 'published', 'passes', 'ai_hint', 'desc', 'x', 'image',
             'topics', 'linkless')


def issue_of(date):
    """期号：所在周的周一日期。"""
    d = datetime.date.fromisoformat(date)
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def load_leads(date, days):
    """读最近 days 天各渠道的原始线索。X 帖子按 data/x_judgments.json 里最新的核验结论重新判断。
    返回 (线索列表, 这次读到的全部 X 帖子链接)。"""
    judg = util.load(util.p('data', 'x_judgments.json'), {})
    leads, x_posts = [], set()
    for path in sorted(glob.glob(util.p('raw', '*', '*.json'))):
        ch = os.path.basename(os.path.dirname(path))
        d = os.path.basename(path)[:-5]
        if not re.match(r'\d{4}-\d\d-\d\d$', d) or d > date or util.days_between(d, date) > days:
            continue
        raw = util.load(path)
        if ch != 'x':
            for l in raw.get('leads', []):
                if l.get('key'):
                    leads.append(dict(l, date=d))
            continue
        for it in raw.get('items', []):
            x_posts.add(it['link'])
            j = judg.get(it['tweet_id'])
            verdict, games = x_ingest.judge(it, j)
            g0 = it.get('game') or {}
            hit = it.get('hit') or {}
            words = list(dict.fromkeys((hit.get('model') or []) + (hit.get('ai') or [])))[:3]
            hint = (j or {}).get('ai_evidence') or ('正文提到 ' + '、'.join(words) + '（规则识别）' if words else '')
            judged_by = (j or {}).get('by') or '规则'
            origin = g0.get('origin') or {}
            if origin and not (j or {}).get('ai_evidence'):
                oh = ai_hint(origin.get('text'))
                hint = (f"原帖（@{origin['handle']}）：{oh}" if oh else '') or hint
            if verdict == 'game':
                for g in games:
                    pv = g.get('post_views') or g0.get('views_now') or it.get('views')
                    src = g.get('source_post') or it['link']
                    if origin and not g.get('source_post'):
                        src, pv = origin['url'], origin.get('views') or pv     # 转发帖：出处记原帖
                    own = src == it['link']
                    leads.append({
                        'channel': 'x', 'date': d, 'title': g.get('name') or '', 'url': g['url'], 'key': g['key'],
                        'source_url': src, 'metrics': {'x_views': pv, 'x_likes': it.get('likes') if own else None},
                        'published': (g.get('post_time_utc') or it.get('publish_time_utc') or '')[:10],
                        'passes': True, 'ai_hint': hint,
                        'desc': (it.get('text') or '')[:400] if own else
                        (origin.get('text') or '')[:400] if src == origin.get('url') else '',
                        'image': g0.get('thumb') or '',
                        'x': {'handle': it['handle'] if own else (re.search(r'x\.com/([^/]+)', src) or [0, ''])[1],
                              'found_how': g.get('how'), 'judged_by': judged_by,
                              'other_links': g0.get('other_links', []), 'via_post': it['link']},
                    })
            elif verdict == 'maybe' and x_ingest.linkless_ok(it, j) and \
                    (it.get('library') or {}).get('strength') != 'dedup':
                # 没找到试玩链接也先入库：去重键用帖子编号，以后找到链接再换成游戏链接
                leads.append({
                    'channel': 'x', 'date': d, 'url': it['link'], 'key': 'x:' + it['tweet_id'],
                    'title': (j or {}).get('name') or it.get('name_guess') or x_ingest.guess_name(it.get('text')),
                    'source_url': it['link'],
                    'metrics': {'x_views': g0.get('views_now') or it.get('views'), 'x_likes': it.get('likes')},
                    'published': (it.get('publish_time_utc') or '')[:10], 'passes': True, 'ai_hint': hint,
                    'desc': (it.get('text') or '')[:400], 'image': g0.get('thumb') or '', 'linkless': True,
                    'x': {'handle': it['handle'], 'found_how': '', 'judged_by': judged_by,
                          'other_links': g0.get('other_links', []), 'via_post': it['link'],
                          'note': (j or {}).get('note') or ''},
                })
    return leads, x_posts


def squash(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def match_library(key, url, name, lib):
    """和收录库比对。返回 (状态, 说明)：已在库 / 疑似仿冒 / None。"""
    if key.startswith('x:'):
        return None, None
    host = util.host_of(url)
    for g in lib:
        if key == g.get('key') or key in g.get('alt_keys', []):
            return '已在库', g['id']
        if '/' not in g.get('key', '/') and host == util.host_of(g['url']):
            return '已在库', g['id']
    if '/' not in key:                               # 独立域名才查仿冒
        label = squash(host.rsplit('.', 1)[0])
        for g in lib:
            if '/' in g.get('key', '/') or g.get('key', '').startswith('x:'):
                continue
            ghost = util.host_of(g['url'])
            gl = squash(util.root_domain(ghost).rsplit('.', 1)[0])
            if len(gl) >= 7 and gl in label and util.root_domain(host) != util.root_domain(ghost):
                return '疑似仿冒', g['id']
    return None, None


def x_views(c, l):
    """一条 X 线索现在的浏览数：采集时的值和后来重读的值里取大的。"""
    now = ((c.get('x_now') or {}).get(l['source_url']) or {}).get('views') or 0
    return max(l['metrics'].get('x_views') or 0, now)


def best_metrics(c):
    """每种数字取各条线索里最大的一个。返回 {指标: (值, 线索)}。"""
    best = {}
    for l in c['leads']:
        for k, v in l['metrics'].items():
            if v is None or k not in METRIC_LABEL:
                continue
            if k == 'x_views':
                v = x_views(c, l)
            if k not in best or (k != 'itch_rank' and int(v) > int(best[k][0])):
                best[k] = (v, l)
    return best


def signals(c, cfg):
    """重点信号。返回 [{'类别','说明'}]；类别为"技术"的只作说明，不算重点。"""
    H = cfg['thresholds']['hot']
    best = best_metrics(c)
    val = lambda k: int((best.get(k) or (0,))[0] or 0)
    spread = []
    if val('x_views') >= H['x_views']:
        spread.append(f"X 单帖浏览 {util.fmt_num(val('x_views'))}")
    if val('hn_points') >= H['hn_points']:
        spread.append(f"Hacker News {val('hn_points')} 分")
    if val('github_stars') >= H['github_stars']:
        spread.append(f"GitHub {util.fmt_num(val('github_stars'))} 星")
    if val('threejs_views') >= H['threejs_views']:
        spread.append(f"three.js 论坛 {util.fmt_num(val('threejs_views'))} 次浏览")
    out = {}
    if spread:
        out['传播'] = '，'.join(spread)
    plays = max((val(k) for k in PLAYS), default=0)
    if plays >= H['plays']:
        out['规模'] = f'平台显示游玩 {util.fmt_num(plays)} 次'
    if any('store.steampowered.com' in u for l in c['leads'] for u in (l.get('x') or {}).get('other_links', [])):
        out['商业'] = '同时有 Steam 页面'
    text = ' '.join((l.get('title') or '') + ' ' + (l.get('desc') or '') for l in c['leads'])
    if TECH.search(text) and TECH_PLUS.search(text):
        out['技术'] = '线索原文提到：' + '、'.join(dict.fromkeys(
            m.group(0).lower() for m in list(TECH.finditer(text))[:2] + list(TECH_PLUS.finditer(text))[:2]))
    for s in c.get('manual_signals', []):            # 人工或每周核验补充的信号，如 {"类别":"玩法","说明":"…"}
        out[s['类别']] = s['说明']
    return [{'类别': k, '说明': v} for k, v in out.items()]


def is_hot(sigs):
    return any(s['类别'] != '技术' for s in sigs)


def reasons_of(c):
    """入库理由：每个渠道一句，写明在哪里看到、当时的数字。"""
    out = []
    for l in c['leads']:
        m, ch = l['metrics'], l['channel']
        if ch == 'x':
            how = '核验确认' if (l.get('x') or {}).get('judged_by') not in (None, '规则') else '规则识别'
            s = f"X 帖子（@{(l.get('x') or {}).get('handle', '')}，{how}），浏览 {util.fmt_num(x_views(c, l))}"
        elif ch == 'hn':
            s = f"Hacker News {m.get('hn_points')} 分"
        elif ch == 'threejs':
            s = f"three.js 论坛作品区，{util.fmt_num(m.get('threejs_views'))} 次浏览"
        elif ch == 'github':
            s = f"GitHub {util.fmt_num(m.get('github_stars'))} 星"
        elif ch == 'itch':
            s = f"itch.io 网页新品榜第 {m.get('itch_rank')} 名"
        elif ch == 'awesome':
            s = 'AI 游戏精选清单新增条目'
        elif ch in CHARTS:
            s = CH_NAME[ch] + ('新上榜' if m.get('chart_new_entry') else '在榜')
            if l.get('published'):
                s += f"，{l['published']} 发布"
        else:
            s = CH_NAME.get(ch, ch)
        if s not in out:
            out.append(s)
    return out


def ai_tag_of(c, cfg):
    if c.get('ai_tag_manual'):
        return c['ai_tag_manual'], c.get('ai_evidence', '')
    host = util.host_of(c['url'])
    for l in c['leads']:
        if l.get('ai_hint'):
            who = {'x': '来源帖子', 'hn': 'Hacker News 标题', 'github': '仓库描述', 'threejs': '论坛帖子',
                   'itch': 'itch.io 描述'}.get(l['channel'], '')
            return 'ai', (who + '：' if who else '') + l['ai_hint']
    if is_ai_platform(c['url'], cfg):
        return 'ai', f'发布在 AI 创作平台（{host}）'
    return 'unlabeled', ''


def is_ai_platform(url, cfg):
    return host_in(util.host_of(url), cfg['game_hosts']['ai_platforms'])


ENGINE_HTML = re.compile(r'<canvas|three(\.module)?(\.min)?\.js|webgl|webgpu|phaser|pixi|babylon|playcanvas|unity|godot', re.I)
ENGINE_JS = re.compile(r'WebGLRenderer|getContext\(["\'](?:webgl|webgpu|2d)|createElement\(["\']canvas|navigator\.gpu|'
                       r'Phaser\.|PIXI\.|BABYLON\.|createUnityInstance|pc\.Application', re.I)


def page_meta(url):
    """读游戏页面的标题、描述、封面图地址，并看页面里有没有画布或游戏引擎的痕迹。
    很多游戏的首页只有一个空壳加打包脚本，所以首页没找到时再看前几个同站脚本。"""
    from urllib.parse import urljoin
    import html as _h
    h = net.get(url, timeout=30, maxb=15_000_000)     # 有的游戏整个打包在一个网页文件里，体积很大
    if not h:
        return {'ok': False}

    def m(p):
        x = re.search(p, h, re.S | re.I)
        return _h.unescape(re.sub(r'\s+', ' ', x.group(1)).strip()) if x else ''

    def meta(name):
        return (m(rf'<meta[^>]+(?:property|name)=["\']{name}["\'][^>]+content=["\']([^"\']*)') or
                m(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{name}["\']'))
    img = meta('og:image') or meta('twitter:image')
    icon = m(r'<link[^>]+rel=["\'](?:apple-touch-icon|icon|shortcut icon)["\'][^>]+href=["\']([^"\']+)')
    how = '首页' if ENGINE_HTML.search(h) else ''
    if not how:
        srcs = [urljoin(url, s) for s in re.findall(r'<script[^>]+src=["\']([^"\']+)', h)]
        for s in [s for s in srcs if util.root_domain(util.host_of(s)) == util.root_domain(util.host_of(url))][:3]:
            if ENGINE_JS.search(net.get(s, timeout=20, maxb=4_000_000) or ''):
                how = '页面脚本'
                break
    return {'ok': True, 'title': m(r'<title[^>]*>(.*?)</title>')[:120],
            'desc': (meta('og:description') or meta('description'))[:400],
            'image': urljoin(url, img) if img else '', 'icon': urljoin(url, icon) if icon else '',
            'has_canvas': bool(how), 'canvas_how': how}


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


def gameness(c, kind, cfg):
    """判断是不是能在浏览器里玩的游戏。返回 (是否, 链接情况, 不通过的原因)。
    链接情况：ok 有试玩链接；repo 只有代码仓库；none 还没找到试玩链接。"""
    host = util.host_of(c['url'])
    # 不用再看页面的来源：精选清单、three.js 论坛作品区（过线时已要求像游戏），以及核验过的 X 帖子
    trusted = any((l['channel'] in ('awesome', 'threejs') and l.get('passes')) or
                  (l['channel'] == 'x' and (l.get('x') or {}).get('judged_by') not in (None, '规则')) for l in c['leads'])
    if kind == 'none':
        return True, 'none', []
    if c.get('is_game_manual'):                      # 人工确认过是网页游戏（例如纯网页元素做的、没有画布的游戏）
        return True, 'repo' if kind == 'repo' else 'ok', []
    if kind in ('game', 'site'):
        gh = cfg['game_hosts']
        if trusted or is_ai_platform(c['url'], cfg) or host_in(host, gh['exact']) or host.endswith('itch.io'):
            return True, 'ok', []
        if not (c.get('page') or {}).get('ok'):
            c['page'] = page_meta(c['url'])
        if not c['page'].get('ok'):
            return False, '', ['试玩页面暂时打不开，下次运行再看']
        if c['page'].get('has_canvas'):
            return True, 'ok', []
        return False, '', ['页面和脚本里没有发现画布或游戏引擎，可能不是游戏']
    if kind == 'repo':
        if trusted:
            return True, 'repo', []
        return False, '', ['只有代码仓库链接，没有试玩页面']
    return False, '', [{'article': '链接是文章页，不是试玩页面'}.get(kind, '链接不是试玩页面')]


def build_stats(c):
    stats = []
    for k, (v, l) in best_metrics(c).items():
        if k == 'hn_points':
            val = f"{v} 分 / {l['metrics'].get('hn_comments') or 0} 评论"
        elif k == 'threejs_views':
            val = f'{util.fmt_num(v)} 次浏览'
        elif k == 'github_stars':
            val = f"{util.fmt_num(v)} 星" + (f" / {util.fmt_num(l['metrics']['github_forks'])} 分叉" if l['metrics'].get('github_forks') else '')
        elif k == 'itch_rank':
            val = f'第 {v} 名'
        elif k == 'wavedash_playing':
            val = f'{util.fmt_num(v)} 人'
        elif k == 'x_views':
            val = util.fmt_num(v)
        else:
            val = util.fmt_num(v) + ' 次'
        label, doubt = METRIC_LABEL[k]
        stats.append({'label': label, 'value': val, 'doubt': doubt})
    return stats


def card_dynamic(c, cfg, date):
    """卡片上随数据变化的部分：数字、重点信号、来源、入库理由、链接。每次合并都重算。"""
    pub = c.get('published') or ''
    return {
        'url': c['url'], 'key': c['key'], 'link_status': c.get('link_status') or 'ok',
        'date_text': (f'{pub} 发帖' if c['key'].startswith('x:') else f'{pub} 首次公开') if pub else '首次公开日期未确认',
        'stats': build_stats(c), 'signals': c['signals'], 'reasons': reasons_of(c),
        'ai_tag': c['ai_tag'], 'ai_evidence': c['ai_evidence'],
        'sources': [{'channel': l['channel'], 'url': l['source_url'], 'date': l['date'],
                     'metrics': dict(l['metrics'], **({'x_views': x_views(c, l)} if l['channel'] == 'x' else {}))}
                    for l in c['leads']],
        'x_handles': sorted({l['x']['handle'] for l in c['leads'] if l.get('x') and l['x'].get('handle')}),
        'source_posts': sorted({l['source_url'] for l in c['leads'] if l['channel'] == 'x'}),
        'stats_date': date,
    }


def ensure_images(card, c):
    """重点卡片才抓图。没有试玩页面的用来源帖子的视频封面。"""
    if card.get('shot') or card.get('images_tried'):
        return
    meta = c.get('page') or {}
    if card['link_status'] == 'ok' and not meta.get('ok'):
        meta = c['page'] = page_meta(c['url'])
    card['icon'] = card.get('icon') or fetch_image(meta.get('icon'), card['id'], 'icon')
    card['shot'] = fetch_image(meta.get('image') or next((l.get('image') for l in c['leads'] if l.get('image')), ''),
                               card['id'], 'shot')
    card['images_tried'] = True


def make_card(c, cfg, date, lib_ids):
    """把入库的线索生成一张自动卡片：只放能追溯到出处的原文和数字，不写主观描述。"""
    meta = c.get('page') or {}
    handle = next((l['x']['handle'] for l in c['leads'] if l.get('x') and l['x'].get('handle')), '')
    name = c.get('name_manual') or c['name'] or meta.get('title') or \
        (f'@{handle} 的作品（未命名）' if c['key'].startswith('x:') else c['key'])
    gid = base = util.slug(name) or util.slug(c['key'])
    n = 2
    while gid in lib_ids:
        gid = f'{base}-{n}'
        n += 1
    src_bits = []
    for l in c['leads']:
        b = CH_SRC.get(l['channel'])
        if b and b not in src_bits:
            src_bits.append(b)
    quote_src, quote = '', ''
    if meta.get('desc'):
        quote_src, quote = '页面自述', meta['desc']
    else:
        for l in c['leads']:
            if l.get('desc'):
                quote_src, quote = {'x': '来源帖子原文', 'github': '仓库描述', 'threejs': '论坛首帖原文'}.get(l['channel'], '平台页面描述'), l['desc']
                break
    card = {
        'id': gid, 'name': name, 'kind': '', 'group': '待归类', 'group_type': 'pending',
        'play': '', 'quote': quote, 'quote_src': quote_src, 'tech': '', 'xcheck': '',
        'src': '；'.join(src_bits), 'hot': False, 'status': 'auto',
        'first_seen': c['first_seen'], 'added': date, 'issue': issue_of(date),
        'icon': None, 'shot': None, 'extra_shots': [],
    }
    card.update(card_dynamic(c, cfg, date))
    return card


def refresh_x(cands, cfg, date):
    """重读近期 X 来源帖的浏览数，让升温的游戏能及时标成重点。"""
    days = cfg['thresholds'].get('x_refresh_days', 14)
    n = 0
    for c in cands.values():
        if c['status'] in ('拒绝', '已在库'):
            continue
        for l in c['leads']:
            if l['channel'] != 'x' or not l.get('published') or util.days_between(l['published'], date) > days:
                continue
            m = STATUS.search(l['source_url'])
            tw = x_ingest.fx_tweet(m.group(1), m.group(2)) if m else {}
            if tw.get('views'):
                c.setdefault('x_now', {})[l['source_url']] = {'views': tw['views'], 'date': date}
                n += 1
    return n


def run(date=None, publish=True, refresh=True):
    cfg = util.config()
    date = date or util.today()
    lib = util.load(util.p('data', 'games.json'), [])
    cands = util.load(util.p('data', 'candidates.json'), {})
    leads, x_posts = load_leads(date, cfg['window']['new_days'])
    by_id = {g['id']: g for g in lib}

    # X 线索每次按最新的核验结论重建：先拿掉这次读到的帖子留下的旧线索
    for c in cands.values():
        c['leads'] = [l for l in c['leads'] if not (l['channel'] == 'x' and (l.get('x') or {}).get('via_post') in x_posts)]

    for l in leads:
        if l['key'] not in cands and not l.get('passes'):
            continue                                 # 没过渠道入库线的线索不新建条目，只留在 raw/ 里
        if l['key'] not in cands:
            cands[l['key']] = {'key': l['key'], 'url': l['url'], 'name': '', 'leads': [],
                               'first_seen': {'date': l['date'], 'channel': l['channel']}, 'status': '候选'}
            # 之前没找到试玩链接、按帖子编号入库的，现在找到了链接：沿用原来的卡片
            if l['channel'] == 'x' and not l.get('linkless'):
                for post in (l['x'].get('via_post'), l['source_url']):
                    m = STATUS.search(post or '')
                    old = cands.pop('x:' + m.group(2), None) if m else None
                    if old and old.get('game_id'):
                        cands[l['key']].update(status=old['status'], game_id=old['game_id'], first_seen=old['first_seen'],
                                               x_now=old.get('x_now', {}))
                        break
        c = cands[l['key']]
        sig = (l['channel'], l['source_url'])
        old = next((x for x in c['leads'] if (x['channel'], x['source_url']) == sig), None)
        slim = {k: l.get(k) for k in LEAD_KEYS if l.get(k) not in (None, '')}
        slim.setdefault('metrics', {})
        if old:
            first = old['date']
            old.update(slim)                         # 同一出处：数字更新为最新一次读到的
            old['date'], old['last_date'] = first, l['date']
        else:
            c['leads'].append(slim)
        if l['date'] < c['first_seen']['date']:
            c['first_seen'] = {'date': l['date'], 'channel': l['channel']}

    if refresh:
        n = refresh_x(cands, cfg, date)
        print(f'[merge] 重读了 {n} 条 X 来源帖的浏览数')

    lib_ids = set(by_id)
    new_cards, new_hot, dropped = [], [], []
    for key, c in list(cands.items()):
        card = by_id.get(c.get('game_id') or '')
        # 来源帖核验后被判否、线索清空的：撤下自动卡片
        if not c['leads'] or c['status'] == '拒绝':
            if not c['leads'] and c['status'] != '拒绝':
                if c['status'] != '收录':
                    del cands[key]
                    continue
                c.update(status='拒绝', reject_reason='来源帖子核验后判断不是网页游戏，或已并入其他条目')
            if card and card.get('status') == 'auto':
                lib.remove(card)
                by_id.pop(card['id'])
                dropped.append(card['name'])
            continue
        c['name'] = c.get('name_manual') or next((l['title'] for l in c['leads'] if l.get('title')
                                                   and l['channel'] != 'hn' and l['channel'] != 'github'), '') \
            or next((l['title'] for l in c['leads'] if l.get('title')), '')
        c['name'] = re.sub(r'^(Show|Launch) HN:\s*', '', c['name'])
        pubs = sorted(l['published'] for l in c['leads'] if l.get('published'))
        c['published'] = pubs[0] if pubs else ''
        c['ai_tag'], c['ai_evidence'] = ai_tag_of(c, cfg)
        c['signals'] = signals(c, cfg)
        c['updated'] = date
        if c['status'] == '收录':
            # 已入库：自动卡片的数字、重点标记和来源跟着最新数据走；核验过的卡片不动
            if card and card.get('status') == 'auto':
                was_hot = card.get('hot')
                c['link_status'] = 'none' if key.startswith('x:') else c.get('link_status') or 'ok'
                card.update(card_dynamic(c, cfg, date))
                card['hot'] = bool(card.get('hot_manual')) or is_hot(c['signals'])
                if card['hot'] and not was_hot:
                    card['hot_since'] = date
                    new_hot.append(card['name'])
                if card['hot']:
                    ensure_images(card, c)
            continue
        st, gid = match_library(key, c['url'], c['name'], lib)
        if st == '已在库':
            c.update(status='已在库', library_id=gid, reasons=[f'试玩链接和已收录的 {gid} 相同'])
            continue
        if st == '疑似仿冒':
            c.update(status='拒绝', library_id=gid, reject_reason=f'域名包含已收录游戏 {gid} 的名字，疑似仿冒或镜像站')
            continue
        kind = 'none' if key.startswith('x:') else classify_link(c['url'], cfg)
        if not any(l.get('passes') for l in c['leads']):
            c.update(status='候选', reasons=['现在没有过入库线的线索'])
            continue
        ok, link_status, why = gameness(c, kind, cfg)
        age = util.days_between(c['published'], date) if c['published'] else None
        reheat = any(l.get('passes') and util.days_between(l.get('last_date') or l['date'], date) <= cfg['window']['reheat_days']
                     and l['channel'] in ('x', 'hn', 'awesome') + CHARTS for l in c['leads'])
        recent = (age is not None and age <= cfg['window']['new_days']) or reheat
        if not recent:
            why.append('首次公开超过 30 天，且近 7 天没有新的热度' if age is not None else '首次公开日期未知')
        c['hard'] = {'是网页游戏': ok, '近期': recent}
        if ok and recent:
            c.update(status='收录', link_status=link_status, reasons=reasons_of(c))
            if publish:
                card = make_card(c, cfg, date, lib_ids)
                card['hot'] = is_hot(c['signals'])
                if card['hot']:
                    card['hot_since'] = date
                    ensure_images(card, c)
                lib.append(card)
                lib_ids.add(card['id'])
                by_id[card['id']] = card
                c['game_id'] = card['id']
                new_cards.append(card)
        elif is_hot(c['signals']) and kind in ('game', 'site', 'repo'):
            c['status'] = '观察'
            c['reasons'] = [f"{s['类别']}：{s['说明']}" for s in c['signals'] if s['类别'] != '技术'] + why
        else:
            c['status'] = '候选'
            c['reasons'] = why or ['没有过入库线']

    util.save(util.p('data', 'candidates.json'), cands)
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
    print(f'[merge] {date} 线索 {len(leads)} → 条目 {len(cands)}：{cnt}；本次新入库 {len(new_cards)} 款'
          f'（重点 {sum(1 for g in new_cards if g["hot"])}），新晋重点 {new_hot}，撤下 {dropped}')
    return {'date': date, 'counts': cnt, 'new': [c['id'] for c in new_cards],
            'new_hot': [c['id'] for c in new_cards if c['hot']] + new_hot, 'dropped': dropped}


if __name__ == '__main__':
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None)
