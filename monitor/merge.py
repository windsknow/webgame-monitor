# -*- coding: utf-8 -*-
"""合并去重 → 判断是不是网页游戏 → 入库 → 标重点。

读 raw/<渠道>/<日期>.json（最近 30 天）和 raw/x/<日期>.json，写：
  data/candidates.json   线索队列（按去重键）。status：收录 / 观察 / 候选 / 已在库 / 拒绝
  data/sources.json      每条线索的原始出处，按去重键归档，用于追溯
  data/games.json        入库的游戏。自动入库的卡片 status=auto，等每周核验补全文字

2026-11-12 起的规则：过了渠道入库线、判断为网页游戏、并且是近期的，直接入库，
不要求传播数据、游玩次数或试玩链接。

2026-11-25 起的重点规则（用户定稿，口径见 docs/重点评判规则.md）：X 单帖浏览到重点线，或每周打分里
玩法新颖（N）、技术前沿（T）有一项得 2 分（待核、同类移植不算），就成为重点（hot）。入选理由写进 hot_why，
只做展示的标签（X 有传播、平台爆款、待核、同类移植）写进 labels。已核验卡片也按这条重算。

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
CHARTS = ('spawn', 'verse8', 'astrocade', 'rosebud', 'wavedash', 'fable52')
# 渠道读到的数字 → 卡片上的标签。第三项为 True 的是口径存疑的数字（页面标红，不参与重点判断）
METRIC_LABEL = {
    'x_views': ('X 帖子浏览', False), 'hn_points': ('Hacker News', False), 'threejs_views': ('three.js 论坛', False),
    'github_stars': ('GitHub', False), 'itch_rank': ('itch.io 网页新品榜', False),
    'spawn_plays': ('Spawn 游玩数', False), 'astrocade_plays': ('Astrocade 游玩数', False),
    'rosebud_plays': ('Rosebud 游玩数', False), 'verse8_sessions': ('Verse8 游玩数', True),
    'wavedash_playing': ('Wavedash 实时在线', False), 'fable52_plays': ('Fable52.games 站内游玩数', False),
}
PLAYS = ('spawn_plays', 'astrocade_plays', 'rosebud_plays')
CH_NAME = {'x': 'X 帖子', 'hn': 'Hacker News', 'threejs': 'three.js 论坛', 'github': 'GitHub', 'itch': 'itch.io 网页新品榜',
           'awesome': 'AI 游戏精选清单', 'spawn': 'Spawn 热榜', 'verse8': 'Verse8 热门', 'astrocade': 'Astrocade 热门页',
           'rosebud': 'Rosebud 首页精选', 'wavedash': 'Wavedash 热门', 'fable52': 'Fable52.games 合集站'}
CH_SRC = {'x': 'X 帖子数据读取自公开接口', 'hn': 'Hacker News 官方接口', 'threejs': 'three.js 官方论坛接口',
          'github': 'GitHub 接口', 'itch': 'itch.io 榜单', 'awesome': 'AI 游戏精选清单', 'spawn': 'Spawn 平台页面显示值',
          'verse8': 'Verse8 平台页面显示值', 'astrocade': 'Astrocade 平台页面显示值', 'rosebud': 'Rosebud 平台页面显示值',
          'wavedash': 'Wavedash 平台页面显示值', 'fable52': 'Fable52.games 页面显示值'}
LEAD_KEYS = ('channel', 'date', 'title', 'source_url', 'metrics', 'published', 'passes', 'ai_hint', 'desc', 'x', 'image',
             'topics', 'linkless', 'author', 'author_url', 'repo', 'mirror_url', 'origin', 'article', 'matched')


def issue_of(date):
    """期号：所在周的周一日期。"""
    d = datetime.date.fromisoformat(date)
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def not_game_repo(l):
    """合集站把作者的工具包或技能合集仓库当成"源码仓库"时，仓库的星数不是这款游戏的热度，不计入。
    这类仓库列在 config.json 的 repo_not_game 里（每周核验时发现就加）。"""
    repo = (l.get('repo') or (l.get('source_url') if l.get('channel') == 'github' else '') or '').lower().rstrip('/')
    bad = [r.lower() for r in util.config().get('repo_not_game', [])]
    if repo and any(repo.endswith('github.com/' + r) for r in bad) and 'github_stars' in (l.get('metrics') or {}):
        l['metrics'] = {k: v for k, v in l['metrics'].items() if k not in ('github_stars', 'github_forks')}
    return l


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
                    leads.append(not_game_repo(dict(l, date=d)))
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


XREADS = {}   # data/state/x_reads.json：{帖子链接: {'views', 'likes', 'date'}}，run() 开头读进来，refresh_x 更新


def post_of(l):
    """一条线索对应的 X 帖子：X 渠道是来源帖本身，二手渠道是溯源找到的作者帖或他人介绍帖。"""
    if l['channel'] == 'x':
        return l['source_url']
    return (l.get('origin') or {}).get('x') or ''


def x_read(c, l):
    """这条线索的 X 帖子最近一次重读的结果：{'views','date'}，没读过的返回 {}。"""
    u = post_of(l)
    return XREADS.get(u) or (c.get('x_now') or {}).get(u) or {}


def x_views(c, l):
    """一条线索现在的 X 浏览数：采集时的值和后来重读的值里取大的（浏览只涨不跌）。"""
    return max(l['metrics'].get('x_views') or 0, x_read(c, l).get('views') or 0)


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


def x_line(views, asof, kind=''):
    return f"X 单帖浏览 {util.fmt_num(views)}（{kind + '，' if kind else ''}{asof[5:] + ' 读取' if asof else '读取日期未记'}）"


def signals(c, cfg):
    """数据信号，返回 [{'类别','说明'}]。只有"X 热度"让卡片成为重点；"X 有传播""平台爆款"只做展示；
    "技术"是线索原文里出现的技术词，只作说明。游玩数、HN 分、GitHub 星不再是重点信号（2026-11-25 起）。"""
    T = cfg['thresholds']
    best = best_metrics(c)
    val = lambda k: int((best.get(k) or (0,))[0] or 0)
    out = {}
    if 'x_views' in best:
        v, l = best['x_views']
        kind = ('他人介绍帖' if (l.get('origin') or {}).get('x_kind') == 'third_party' else '作者帖子') \
            if l['channel'] != 'x' else ''
        asof = x_read(c, l).get('date') or l.get('last_date') or l['date']
        if int(v) >= T['hot']['x_views']:
            out['X 热度'] = x_line(v, asof, kind)
        elif int(v) >= T.get('x_spread', 50000):
            out['X 有传播'] = x_line(v, asof, kind)
    for k, line in (T.get('platform_hit') or {}).items():
        if not k.startswith('_') and val(k) >= line:
            out['平台爆款'] = f"{METRIC_LABEL[k][0]} {util.fmt_num(val(k))} 次（≥ {util.fmt_num(line)}）"
    text = ' '.join((l.get('title') or '') + ' ' + (l.get('desc') or '') for l in c['leads'])
    if TECH.search(text) and TECH_PLUS.search(text):
        out['技术'] = '线索原文提到：' + '、'.join(dict.fromkeys(
            m.group(0).lower() for m in list(TECH.finditer(text))[:2] + list(TECH_PLUS.finditer(text))[:2]))
    return [{'类别': k, '说明': v} for k, v in out.items()]


def x_hot(sigs):
    return any(s['类别'] == 'X 热度' for s in sigs or [])


def verified_x(g, cfg):
    """已核验卡片的 X 数字：从 source_posts 的重读结果更新 stats 里的帖子浏览数，标上读取日期。返回数据信号。"""
    best, asof = 0, ''
    for u in g.get('source_posts') or []:
        r = XREADS.get(u) or {}
        if (r.get('views') or 0) > best:
            best, asof = r['views'], r.get('date', '')
    if not best:
        return []
    st = g.setdefault('stats', [])
    i = next((i for i, x in enumerate(st) if re.fullmatch(r'(X )?(首发帖|作者帖|帖子)浏览', x.get('label', ''))), None)
    if i is None:
        st.insert(0, {'label': 'X 帖子浏览', 'value': util.fmt_num(best), 'doubt': False, 'asof': asof})
    else:
        lab = st[i]['label']
        st[i] = dict(st[i], label=lab if lab.startswith('X ') else 'X ' + lab, value=util.fmt_num(best), asof=asof)
    T = cfg['thresholds']
    if best >= T['hot']['x_views']:
        return [{'类别': 'X 热度', '说明': x_line(best, asof)}]
    if best >= T.get('x_spread', 50000):
        return [{'类别': 'X 有传播', '说明': x_line(best, asof)}]
    return []


def judge(g, sigs):
    """重点判断：X 热度，或玩法新颖 / 技术前沿有一项 2 分（待核、同类移植不算）。几条同时成立时全部列出。
    写 g['hot']、g['hot_why']（[{'k': 入选理由, 'v': 依据}]）、g['labels']（只做展示的标签，同样格式）。"""
    why, labels = [], []
    for s in sigs or []:
        if s['类别'] == 'X 热度':
            why.append({'k': 'X 热度', 'v': s['说明']})
        elif s['类别'] in ('X 有传播', '平台爆款'):
            labels.append({'k': s['类别'], 'v': s['说明']})
    nv = g.get('novelty') or {}
    for dim, name in (('N', '玩法新'), ('T', '技术强')):
        if nv.get(dim) != 2:
            continue
        if dim in (nv.get('pending') or []):
            labels.append({'k': '待核', 'v': f"{name}待核：{nv.get(dim + '_why', '')}"})
        elif nv.get('dup_of') and dim == 'T':
            labels.append({'k': '同类移植', 'v': nv.get('dup_note') or f"同一原作的移植，重点只取 {nv['dup_of']}"})
        else:
            why.append({'k': name, 'v': nv.get(dim + '_why', '')})
    g['hot_why'], g['labels'] = why, labels
    g['hot'] = bool(why)
    return g['hot']


def prescreen(g, c, cfg):
    """每天预筛（不调模型）：还没打过分的自动卡片，排进本周玩法 / 技术复核的优先级。不决定重点。
    返回 {'level': 优先 / 普通 / 降级, 'why'}。"""
    P = cfg.get('prescreen') or {}
    leads = (c or {}).get('leads') or []
    text = ' '.join(str(g.get(k) or '') for k in ('name', 'kind', 'play', 'quote', 'highlight', 'tech')) + ' ' + \
        ' '.join((l.get('title') or '') + ' ' + (l.get('desc') or '') + ' ' + ' '.join(l.get('topics') or []) for l in leads)
    low = text.lower()
    hit = lambda ws: [w for w in ws if (re.search(r'(?<![a-z])' + re.escape(w.lower()), low) if w.isascii() else w in text)]
    skin, tags = hit(P.get('reskin_words', [])), hit(P.get('review_tags', []))
    chs = {l['channel'] for l in leads}
    doc = bool(g.get('videos')) or any(s['channel'] in ('github', 'x') for s in g.get('sources') or [])
    if chs and chs <= set(P.get('chart_channels', [])):
        rank = min((v for l in leads for k, v in l['metrics'].items() if k.endswith('_rank') and isinstance(v, (int, float))),
                   default=999)
        if skin:
            return {'level': '降级', 'why': f"平台榜单作品，命中换皮关键词：{'、'.join(skin[:3])}"}
        if rank <= P.get('chart_top_n', 5):
            return {'level': '普通', 'why': f'平台榜单作品，榜上最高第 {rank} 名'}
        return {'level': '降级', 'why': f'平台榜单作品，不在榜单前 {P.get("chart_top_n", 5)}'}
    if skin and not tags:
        return {'level': '降级', 'why': f"命中换皮关键词：{'、'.join(skin[:3])}，没有复核技术标签"}
    if tags or doc:
        return {'level': '优先', 'why': ('复核技术标签：' + '、'.join(tags[:4])) if tags else '有作者帖子、源码仓库或视频'}
    return {'level': '普通', 'why': '没有命中换皮关键词，也没有复核技术标签'}


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
            if l.get('article', {}).get('url'):
                s += '（帖子是一篇文章，试玩链接取自文章正文）'
        elif ch == 'threejs':
            s = f"three.js 论坛作品区，{util.fmt_num(m.get('threejs_views'))} 次浏览"
        elif ch == 'github':
            s = f"GitHub {util.fmt_num(m.get('github_stars'))} 星"
        elif ch == 'itch':
            s = f"itch.io 网页新品榜第 {m.get('itch_rank')} 名"
        elif ch == 'awesome':
            s = 'AI 游戏精选清单新增条目'
        elif ch == 'fable52':
            o = l.get('origin') or {}
            s = 'Fable52.games 合集站收录（二手渠道）'
            if o.get('how') and o['how'] != '合集站给的链接':
                s += "，试玩链接取自" + (' ' if o['how'][:1].isascii() else '') + o['how']
            if m.get('x_views'):
                s += f"，{'他人介绍帖' if o.get('x_kind') == 'third_party' else '作者帖子'}浏览 {util.fmt_num(m['x_views'])}"
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
            if l['channel'] == 'hn' and l.get('read') in ('article', 'page'):
                who = 'Hacker News 链接的' + ('文章正文' if l['read'] == 'article' else '页面')
            if l['channel'] == 'fable52' and (l.get('origin') or {}).get('x') and not l['ai_hint'].startswith('Fable52'):
                who = ('他人介绍帖' if l['origin'].get('x_kind') == 'third_party' else '作者帖子') \
                    if l['ai_hint'][:60] in (l['origin'].get('x_text') or '') else '源码仓库描述'
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
    trusted = any((l['channel'] in ('awesome', 'threejs', 'fable52') and l.get('passes')) or
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
        if k == 'x_views':
            stats[-1]['asof'] = x_read(c, l).get('date') or l.get('last_date') or l['date']
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
                    for l in c['leads']] + origin_sources(c),
        'x_handles': sorted({l['x']['handle'] for l in c['leads'] if l.get('x') and l['x'].get('handle')}),
        'source_posts': sorted({l['source_url'] for l in c['leads'] if l['channel'] == 'x'}),
        'stats_date': date,
    }


def origin_sources(c):
    """二手渠道溯源找到的原始出处（作者 X 帖、源码仓库、HN 帖），作为来源链接放进卡片。不算发现渠道。"""
    out, seen = [], {l['source_url'] for l in c['leads']}
    for l in c['leads']:
        o = l.get('origin') or {}
        for ch, k in (('x', 'x'), ('github', 'repo'), ('hn', 'hn')):
            u = o.get(k)
            if u and u not in seen:
                seen.add(u)
                out.append({'channel': ch, 'url': u, 'date': l['date'], 'metrics': {}, 'origin': True})
    return out


def ensure_images(card, c):
    """每张自动卡片抓一次图（信息流页面每张卡片都有头图）。没有试玩页面的用来源帖子的视频封面。"""
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


def refresh_x(cands, lib, cfg, date):
    """重读 X 帖子的浏览数，结果存进 XREADS（data/state/x_reads.json）。
    发帖 x_refresh_days 天内的来源帖每天重读，让升温的游戏能及时标成重点；收录卡片关联的其他帖子
    （二手渠道溯源到的作者帖 / 他人介绍帖、已核验卡片的 source_posts）每 x_reread_days 天重读一次。
    读取日期记实际读的那天（回放旧日期合并时也一样）。"""
    T = cfg['thresholds']
    fresh_days, every = T.get('x_refresh_days', 14), T.get('x_reread_days', 7)
    today = datetime.date.today().isoformat()
    want = {}                                        # 帖子链接 → 是否每天读
    for c in cands.values():
        if c['status'] in ('拒绝', '已在库'):
            continue
        for l in c['leads']:
            u = post_of(l)
            if not u:
                continue
            fresh = l['channel'] == 'x' and bool(l.get('published')) and util.days_between(l['published'], date) <= fresh_days
            if fresh or c['status'] in ('收录', '观察'):
                want[u] = want.get(u, False) or fresh
    for g in lib:
        for u in list(g.get('source_posts') or []) + [x['url'] for x in g.get('sources') or [] if x['channel'] == 'x']:
            want.setdefault(u, False)
    n = 0
    for u, daily in want.items():
        r = XREADS.get(u) or {}
        if r.get('date') == today or (not daily and r.get('date') and util.days_between(r['date'], today) < every):
            continue
        m = STATUS.search(u)
        tw = x_ingest.fx_tweet(m.group(1), m.group(2)) if m else {}
        if tw.get('views'):
            XREADS[u] = {'views': max(tw['views'], r.get('views') or 0), 'likes': tw.get('likes'), 'date': today}
            n += 1
    return n


def run(date=None, publish=True, refresh=True):
    cfg = util.config()
    date = date or util.today()
    lib = util.load(util.p('data', 'games.json'), [])
    cands = util.load(util.p('data', 'candidates.json'), {})
    leads, x_posts = load_leads(date, cfg['window']['new_days'])
    by_id = {g['id']: g for g in lib}
    XREADS.clear()
    XREADS.update(util.load(util.p('data', 'state', 'x_reads.json'), {}))

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
        n = refresh_x(cands, lib, cfg, date)
        util.save(util.p('data', 'state', 'x_reads.json'), XREADS)
        print(f'[merge] 重读了 {n} 条 X 帖子的浏览数')

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
            # 已入库：自动卡片的数字和来源跟着最新数据走；核验过的卡片不动。重点在最后统一重算
            if card and card.get('status') == 'auto':
                c['link_status'] = 'none' if key.startswith('x:') else c.get('link_status') or 'ok'
                card.update(card_dynamic(c, cfg, date))
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
                ensure_images(card, c)
                lib.append(card)
                lib_ids.add(card['id'])
                by_id[card['id']] = card
                c['game_id'] = card['id']
                new_cards.append(card)
        elif x_hot(c['signals']) and kind in ('game', 'site', 'repo'):
            c['status'] = '观察'
            c['reasons'] = [f"{s['类别']}：{s['说明']}" for s in c['signals'] if s['类别'] == 'X 热度'] + why
        else:
            c['status'] = '候选'
            c['reasons'] = why or ['没有过入库线']

    # 重点：所有卡片（含已核验的）按同一条规则重算
    cand_of = {c['game_id']: c for c in cands.values() if c.get('game_id') and c['status'] == '收录'}
    new_ids = {g['id'] for g in new_cards}
    for g in lib:
        was_hot = g.get('hot')
        c = cand_of.get(g['id'])
        sigs = c['signals'] if g.get('status') == 'auto' and c else verified_x(g, cfg)
        judge(g, sigs)
        g.pop('hot_manual', None)                    # 2026-11-25 起人工备注并进玩法 / 技术打分，不再单独让卡片成为重点
        if g.get('status') == 'auto' and not g.get('novelty'):
            g['prescreen'] = prescreen(g, c, cfg)
        else:
            g.pop('prescreen', None)
        if g['hot'] and not was_hot:
            g['hot_since'] = date
            if g['id'] not in new_ids:
                new_hot.append(g['name'])
        elif not g['hot']:
            g.pop('hot_since', None)

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
