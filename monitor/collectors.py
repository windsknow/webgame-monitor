# -*- coding: utf-8 -*-
"""固定渠道采集器。每个采集器返回一组线索，原样存到 raw/<渠道>/<日期>.json。

线索的统一格式：
  channel     渠道名
  title       标题
  url         游戏链接（能打开试玩的那个）
  key         去重键
  source_url  线索出处（讨论帖、仓库、榜单页）
  metrics     这个渠道读到的数字，原样保留
  published   首次公开日期 YYYY-MM-DD（渠道给不出就留空）
  passes      是否过了这个渠道的候选门槛
  ai_hint     文字里出现的 AI 参与线索（原文片段），没有就留空
  desc        渠道自带的一句描述（原文）
"""
import datetime
import html
import re
import time
from urllib.parse import quote

from . import net, util

GAMEY = re.compile(r'\b(game|games|play|playable|fps|mmo|mmorpg|rpg|roguelike|shooter|racing|racer|multiplayer|'
                   r'simulator|sim|puzzle|arcade|chess|platformer|sandbox|open[- ]world|quake|doom|minecraft|'
                   r'pok[eé]mon|zelda|tetris|wordle|geoguessr|battle|arena|tycoon|idle|clicker|voxel)\b', re.I)
AI_HINT = re.compile(r'[^.。\n|]*\b(claude|codex|cursor|gpt[- ]?\d?|chatgpt|gemini|grok|copilot|fable|opus|sonnet|'
                     r'vibe[- ]?cod\w*|vibe jam|llm|ai[- ]generated|built with ai|made with ai|ai agents?|'
                     r'one[- ]shot\w*|single prompt)\b[^.。\n|]*', re.I)


def ai_hint(*texts):
    for t in texts:
        m = AI_HINT.search(t or '')
        if m:
            return m.group(0).strip()[:200]
    return ''


TOOL_NAME = re.compile(r'engine|framework|sdk|skills?|agents?|plugin|studio|template|boilerplate|starter|toolkit|'
                       r'(?<![a-z])(kit|lib|ui|api|cli|mcp)(?![a-z])', re.I)
TOOL_DESC = re.compile(r'\b(engine|framework|library|sdk|toolkit|plugin|skill|agents?|harness|template|boilerplate|starter|'
                       r'editor|viewer|installer|manager|extractor|generator|mod)\b', re.I)
PLAYISH = re.compile(r'\bplay\b|\bplayable\b|browser game|game in (your|the) browser|网页游戏|浏览器里玩', re.I)


def toolish(name, desc):
    """仓库是不是工具而不是一款游戏：名字里带引擎、框架、技能包一类的词；或者描述里有这类词、又没说能玩。"""
    return bool(TOOL_NAME.search(name) or (TOOL_DESC.search(desc) and not PLAYISH.search(desc)))


def _date(ts=None, iso=None):
    if iso:
        return iso[:10]
    if ts:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).date().isoformat()
    return ''


def _epoch(date_str):
    d = datetime.date.fromisoformat(date_str)
    return int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp())


def _shift(date_str, days):
    return (datetime.date.fromisoformat(date_str) + datetime.timedelta(days=days)).isoformat()


def floor(cfg, name):
    """入库线：各渠道的线索达到这个数才会被考虑。"""
    return cfg['thresholds']['floors'][name]


WEBBY = re.compile(r'browser|\bweb\b|webgl|webgpu|three\.?js|html5|wasm|webassembly|javascript|typescript|phaser|'
                   r'babylon|pixi|playcanvas|godot web|网页|浏览器', re.I)


# ---------------- Hacker News ----------------
def hn(cfg, date, since=None, min_points=None, step_days=2):
    """按分数全量扫描：取时间窗内所有达到 min_points 的帖子，再用标题判断是不是在讲游戏。
    不用关键词搜索，是因为回测发现关键词会漏（例如标题里没有 game 一词的 "Quake ported to safe Rust"）。"""
    since = since or _shift(date, -cfg['window']['reheat_days'])
    th = floor(cfg, 'hn_points')
    min_points = min_points or th
    seen, leads = {}, []
    a = since
    while a <= date:
        b = min(_shift(a, step_days), _shift(date, 1))
        for page in range(0, 10):
            u = ('https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=100'
                 f'&page={page}&numericFilters='
                 + quote(f'created_at_i>={_epoch(a)},created_at_i<{_epoch(b)},points>={min_points}'))
            d = net.getj(u, retries=2) or {}
            hits = d.get('hits', [])
            for h in hits:
                seen[h['objectID']] = h
            if len(hits) < 100:
                break
        a = b
    for h in seen.values():
        url, title = h.get('url'), h.get('title') or ''
        if not url:
            continue
        host = util.host_of(url)
        if not GAMEY.search(title) and not host.endswith('itch.io'):
            continue
        leads.append({
            'channel': 'hn', 'title': title, 'url': url, 'key': util.game_key(url),
            'source_url': f"https://news.ycombinator.com/item?id={h['objectID']}",
            'metrics': {'hn_points': h.get('points'), 'hn_comments': h.get('num_comments')},
            'published': _date(ts=h.get('created_at_i')),
            'passes': (h.get('points') or 0) >= th,
            'ai_hint': ai_hint(title, h.get('story_text')), 'desc': '',
        })
    return leads


# ---------------- three.js 论坛作品区 ----------------
def threejs(cfg, date, period='weekly', fetch_links=True):
    th = floor(cfg, 'threejs_views_week')
    d = net.getj(f'https://discourse.threejs.org/c/showcase/7/l/top.json?period={period}', retries=2) or {}
    leads = []
    for t in (d.get('topic_list') or {}).get('topics', []):
        passes = (t.get('views') or 0) >= th
        url, hint, body = '', '', ''
        if fetch_links and (passes or (t.get('views') or 0) >= th / 2):
            td = net.getj(f"https://discourse.threejs.org/t/{t['id']}.json") or {}
            posts = (td.get('post_stream') or {}).get('posts') or [{}]
            p0 = posts[0]
            ext = [l['url'] for l in (p0.get('link_counts') or []) if not l.get('internal')
                   and not re.search(r'\.(png|jpe?g|gif|webp|mp4)(\?|$)', l['url'])]
            pref = [u for u in ext if util.host_of(u) not in ('github.com', 'youtube.com', 'youtu.be', 'x.com', 'twitter.com')]
            url = re.sub(r'[?&]utm_[^&#]*', '', (pref or ext or [''])[0])
            body = re.sub(r'<[^>]+>', ' ', p0.get('cooked') or '')
            hint = ai_hint(t.get('title'), body)
            time.sleep(0.3)
        # 作品区里有很多不是游戏的演示和工具：标题或首帖像在讲游戏，或者写明了 AI 参与，才算过线
        passes = passes and bool(GAMEY.search((t.get('title') or '') + ' ' + body) or hint)
        leads.append({
            'channel': 'threejs', 'title': t.get('title') or '', 'url': url, 'key': util.game_key(url),
            'source_url': f"https://discourse.threejs.org/t/{t.get('slug')}/{t['id']}",
            'metrics': {'threejs_views': t.get('views'), 'threejs_replies': max((t.get('posts_count') or 1) - 1, 0),
                        'threejs_likes': t.get('like_count')},
            'published': _date(iso=t.get('created_at')), 'passes': passes, 'ai_hint': hint,
            'desc': re.sub(r'\s+', ' ', body).strip()[:300],
        })
    return leads


# ---------------- GitHub ----------------
def github(cfg, date, since=None, min_stars=None):
    since = since or _shift(date, -cfg['window']['new_days'])
    th = floor(cfg, 'github_stars_30d')
    min_stars = min_stars or th
    seen, errs = {}, []
    for q in cfg['github_queries']:
        u = ('https://api.github.com/search/repositories?sort=stars&order=desc&per_page=50&q='
             + quote(f'{q} created:{since}..{date} stars:>={min_stars}'))
        d = net.getj(u, headers=net.github_headers(), retries=1) or {}
        if 'items' not in d:
            errs.append(str(d.get('message') or '没有返回结果')[:120])
        for r in d.get('items', []) or []:
            seen[r['full_name']] = r
        time.sleep(7 if 'Authorization' not in net.github_headers() else 1)   # 匿名每分钟 10 次
    if errs and not seen:
        raise RuntimeError('GitHub 搜索接口报错：' + errs[0])     # 交给 run() 记到 error 字段里
    leads = []
    hot = cfg['thresholds']['hot']['github_stars']
    for r in seen.values():
        home = (r.get('homepage') or '').strip()
        text = ' '.join([r.get('description') or '', ' '.join(r.get('topics') or [])])
        if not GAMEY.search(text + ' ' + r['name']):
            continue
        url = home if home.startswith('http') else r['html_url']
        leads.append({
            'channel': 'github', 'title': r['name'], 'url': url, 'key': util.game_key(url),
            'alt_keys': [util.game_key(r['html_url'])],
            'source_url': r['html_url'],
            'metrics': {'github_stars': r.get('stargazers_count'), 'github_forks': r.get('forks_count')},
            'published': _date(iso=r.get('created_at')),
            # 过线条件：有试玩主页，并且不是引擎、框架、技能包这类工具；星数到重点线的不看这两条，留给人工判断
            'passes': (r.get('stargazers_count') or 0) >= hot or (
                (r.get('stargazers_count') or 0) >= th and home.startswith('http')
                and util.host_of(home) != 'github.com' and not toolish(r['name'], r.get('description') or '')),
            'ai_hint': ai_hint(text), 'desc': r.get('description') or '',
            'topics': ' '.join(r.get('topics') or []),
        })
    return leads


# ---------------- itch.io 网页平台新游 ----------------
def itch(cfg, date):
    """读"新品与热门·网页平台"榜的 RSS，并累计每款游戏在榜的日期。在榜跨度满两周才过门槛。"""
    xml = net.get('https://itch.io/games/new-and-popular/platform-web.xml', retries=2)
    state_p = util.p('data', 'state', 'itch_chart.json')
    state = util.load(state_p, {})
    leads = []
    weeks = floor(cfg, 'itch_weeks_on_chart')
    for rank, it in enumerate(re.findall(r'<item>(.*?)</item>', xml, re.S), 1):
        g = lambda tag: html.unescape((re.search(rf'<{tag}>(.*?)</{tag}>', it, re.S) or [None, ''])[1]).strip()
        link = g('link')
        if not link:
            continue
        desc = re.sub(r'<!\[CDATA\[|\]\]>|<img[^>]*>', '', g('description')).strip()
        s = state.setdefault(link, {'first': date, 'dates': []})
        if date not in s['dates']:
            s['dates'].append(date)
        span = util.days_between(s['first'], date)
        leads.append({
            'channel': 'itch', 'title': g('plainTitle') or g('title'), 'url': link, 'key': util.game_key(link),
            'source_url': 'https://itch.io/games/new-and-popular/platform-web',
            'metrics': {'itch_rank': rank, 'itch_days_on_chart': len(s['dates']), 'itch_first_on_chart': s['first']},
            'published': _date(iso=_rfc(g('createDate') or g('pubDate'))),
            # 在榜满两周，或者描述里写明了 AI 参与
            'passes': (span >= (weeks - 1) * 7 and len(s['dates']) >= 2) or bool(ai_hint(desc)),
            'ai_hint': ai_hint(desc), 'desc': desc,
        })
    util.save(state_p, state)
    return leads


def _rfc(s):
    try:
        return datetime.datetime.strptime(s, '%a, %d %b %Y %H:%M:%S GMT').date().isoformat()
    except Exception:
        return ''


# ---------------- awesome-ai-built-games ----------------
def awesome(cfg, date, only_new=True):
    """读精选清单的 README，和上次看到的对比，新增的条目全部进候选。首次运行只建立基线。"""
    state_p = util.p('data', 'state', 'awesome_seen.json')
    state = util.load(state_p, None)
    first_run = state is None
    state = state or {}
    leads = []
    for repo in cfg['awesome_repos']:
        md = net.get(f'https://raw.githubusercontent.com/{repo}/HEAD/README.md', retries=2)
        for m in re.finditer(r'^- \[([^\]]+)\]\((https?://[^)\s]+)\)\s*[-–—]\s*(.+)$', md, re.M):
            name, url, desc = m.group(1), m.group(2), m.group(3).strip()
            if 'github.com/' + repo.lower() in url.lower():
                continue
            k = util.game_key(url)
            is_new = k not in state
            if is_new:
                state[k] = {'first': date, 'name': name, 'baseline': first_run}
            if only_new and (not is_new or first_run):
                continue
            leads.append({
                'channel': 'awesome', 'title': name, 'url': url, 'key': k,
                'source_url': f'https://github.com/{repo}',
                'metrics': {'awesome_listed': True}, 'published': '', 'passes': True,
                'ai_hint': '收录于 AI 游戏精选清单（要求大部分代码由 AI 编写）；' + (ai_hint(desc) or ''), 'desc': desc,
            })
    util.save(state_p, state)
    return leads


# ---------------- 榜单类渠道的通用规则 ----------------
def _chart(name, entries, cfg, date):
    """榜单类渠道的通用规则：和以前见过的对比，新进榜的过线；首次运行只建立基线，
    但发布在近 30 天内的新游戏照样过线。entries 里的每一项已经是线索格式（缺 passes）。"""
    if not entries:
        raise RuntimeError('页面结构可能变了：没有解析出任何游戏')
    state_p = util.p('data', 'state', f'chart_{name}.json')
    state = util.load(state_p, None) or {'first_date': date, 'seen': {}}
    first_run = state.get('first_date') == date      # 同一天重跑，结果不变
    seen = state['seen']
    low = (floor(cfg, 'chart_min_plays') or {}).get(name, 0)
    for l in entries:
        k = l['key']
        if low and (l['metrics'].get(f'{name}_plays') or 0) < low and k not in seen:
            l['passes'] = False                      # 还没到最低游玩数：先不算上榜，到了再算新上榜
            l['metrics']['chart_new_entry'] = False
            continue
        is_new = seen.setdefault(k, date) == date
        pub = l.get('published') or ''
        fresh = bool(pub) and util.days_between(pub, date) <= cfg['window']['new_days']
        l['passes'] = bool(is_new and (fresh or not first_run))
        l['metrics']['chart_new_entry'] = bool(is_new and not first_run)
    util.save(state_p, {'first_date': state['first_date'], 'date': date, 'keys': [l['key'] for l in entries], 'seen': seen})
    return entries


# ---------------- Spawn 热榜 ----------------
def spawn(cfg, date):
    """读 Spawn 热榜前 N 名。规则同其他榜单：新进榜的过线；首次运行时只有近 30 天发布的过线。"""
    d = net.getj('https://www.spawn.co/api/feed?sort=top', retries=2) or {}
    entries = []
    for rank, g in enumerate(d.get('games', [])[:floor(cfg, 'spawn_top_n')], 1):
        owner = (g.get('profiles') or {}).get('username') or ''
        gm = g.get('games') or {}
        slug = gm.get('adventure_name') or ''
        if not owner or not slug:
            continue
        url = f'https://www.spawn.co/@{owner}/{slug}'
        entries.append({
            'channel': 'spawn', 'title': gm.get('name') or slug, 'url': url, 'key': util.game_key(url),
            'source_url': 'https://www.spawn.co/',
            'metrics': {'spawn_rank': rank, 'spawn_plays': g.get('play_count'), 'spawn_likes': g.get('like_count')},
            'published': _date(iso=g.get('posted_at')),
            'ai_hint': '发布在 AI 创作平台 Spawn', 'desc': ((g.get('box_copy') or {}).get('blurb') or '')[:300],
            'image': g.get('og_image_url') or g.get('custom_thumbnail_url') or '',
        })
    return _chart('spawn', entries, cfg, date)


# ---------------- AI 创作平台和网页游戏平台的热门页 ----------------
def _flight(page):
    """Next.js 页面内嵌的数据：把 self.__next_f.push 里的字符串拼起来。"""
    import json as _json
    out = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', page):
        try:
            out.append(_json.loads(m.group(1)))
        except ValueError:
            pass
    return ''.join(out)


def _objects_with(text, marker):
    """从内嵌数据里取出所有包含 marker 的 JSON 对象。"""
    import json as _json
    dec, objs, last_end = _json.JSONDecoder(), [], -1
    for m in re.finditer(marker, text):
        if m.start() < last_end:
            continue
        start, depth = m.start(), 0
        while start > 0:
            start -= 1
            if text[start] == '}':
                depth += 1
            elif text[start] == '{':
                if depth == 0:
                    break
                depth -= 1
        try:
            o, n = dec.raw_decode(text[start:])
        except ValueError:
            continue
        objs.append(o)
        last_end = start + n
    return objs


def verse8(cfg, date):
    """Verse8 首页用的接口。读「趋势」和「按游玩数」两个排序的前 N 名。游玩数口径存疑，只记录，不参与重点判断。"""
    n = floor(cfg, 'chart_top_n')
    entries, seen = [], set()
    for sort in ('trending', 'sessions'):
        d = net.getj(f'https://v8-meme-api.verse8.io/v1/verse?page=1&limit={n}&featured=false&sortBy={sort}&excludeHidden=false',
                     retries=2) or {}
        for rank, g in enumerate(d.get('items') or [], 1):
            sid = g.get('verseShortId')
            if not sid or sid in seen:
                continue
            seen.add(sid)
            url = f'https://verse8.io/{sid}'
            entries.append({
                'channel': 'verse8', 'title': g.get('title') or sid, 'url': url, 'key': util.game_key(url),
                'source_url': 'https://verse8.io/',
                'metrics': {'verse8_list': sort, 'verse8_rank': rank, 'verse8_sessions': g.get('sessions'),
                            'verse8_likes': g.get('likeSize')},
                'published': _date(iso=g.get('createdAt')),
                'ai_hint': '发布在 AI 创作平台 Verse8',
                'desc': (g.get('shortDescription') or g.get('description') or '')[:300],
                'image': g.get('imageUrl') or '',
            })
    return _chart('verse8', entries, cfg, date)


def astrocade(cfg, date):
    """Astrocade 热门页。游戏数据内嵌在页面里。"""
    page = net.get('https://www.astrocade.com/category/trending', retries=2, maxb=3_000_000)
    hrefs = {h.rsplit('/', 1)[-1]: h for h in re.findall(r'href="(/games/[^"]+)"', page)}
    entries = []
    for rank, g in enumerate(_objects_with(_flight(page), r'"plays":\d+'), 1):
        gid = g.get('gameId')
        if not gid or not g.get('title'):
            continue
        url = 'https://www.astrocade.com' + (hrefs.get(gid) or f"/games/{util.slug(g['title'])}/{gid}")
        entries.append({
            'channel': 'astrocade', 'title': g['title'], 'url': url, 'key': util.game_key(url),
            'source_url': 'https://www.astrocade.com/category/trending',
            'metrics': {'astrocade_rank': rank, 'astrocade_plays': g.get('plays'), 'astrocade_likes': g.get('likeCount')},
            'published': _date(ts=g.get('firstPublishedTimestamp') or g.get('createdAt')),
            'ai_hint': '发布在 AI 创作平台 Astrocade', 'desc': (g.get('description') or '')[:300],
            'image': g.get('thumbnailUrl') or '',
        })
    return _chart('astrocade', entries[:floor(cfg, 'chart_top_n')], cfg, date)


def rosebud(cfg, date):
    """Rosebud 首页的「Top games made with Rosebud」。页面不给发布日期。"""
    import json as _json
    text = _flight(net.get('https://rosebud.ai/', retries=2, maxb=3_000_000))
    entries = []
    m = re.search(r'"title":"Top games made with Rosebud","games":', text)
    games = []
    if m:
        try:
            games, _ = _json.JSONDecoder().raw_decode(text[m.end():])
        except ValueError:
            games = []
    for rank, g in enumerate(games, 1):
        if not g.get('published_name'):
            continue
        url = f"https://rosebud.ai/play/{g['published_name']}"
        entries.append({
            'channel': 'rosebud', 'title': g.get('name') or g['published_name'], 'url': url, 'key': util.game_key(url),
            'source_url': 'https://rosebud.ai/',
            'metrics': {'rosebud_rank': rank, 'rosebud_plays': g.get('plays_count'), 'rosebud_likes': g.get('likes_count')},
            'published': '', 'ai_hint': '发布在 AI 创作平台 Rosebud', 'desc': '',
            'image': (g.get('preview_image') or '').split('?')[0],
        })
    return _chart('rosebud', entries, cfg, date)


def wavedash(cfg, date):
    """Wavedash 首页的 Trending 区（带实时在线人数）。这个平台不限 AI 作品，所以不带 AI 线索。"""
    page = re.sub(r'<!--.*?-->', '', net.get('https://wavedash.com/', retries=2, maxb=3_000_000), flags=re.S)
    a = page.find('>Trending<')
    b = page.find('>Recommended<', a)
    seg = page[a:b if b > a else len(page)] if a >= 0 else ''
    entries, seen = [], set()
    for m in re.finditer(r'alt="([^"]+)"[^>]*>.{0,400}?>([\d,]+) playing<.{0,400}?href="/games/([a-z0-9-]+)"', seg, re.S):
        name, playing, slug = html.unescape(m.group(1)), int(m.group(2).replace(',', '')), m.group(3)
        if slug in seen:
            continue
        seen.add(slug)
        url = f'https://wavedash.com/games/{slug}'
        entries.append({
            'channel': 'wavedash', 'title': name, 'url': url, 'key': util.game_key(url),
            'source_url': 'https://wavedash.com/',
            'metrics': {'wavedash_rank': len(entries) + 1, 'wavedash_playing': playing},
            'published': '', 'ai_hint': '', 'desc': '',
        })
    return _chart('wavedash', entries, cfg, date)


COLLECTORS = {'hn': hn, 'threejs': threejs, 'github': github, 'itch': itch, 'awesome': awesome, 'spawn': spawn,
              'verse8': verse8, 'astrocade': astrocade, 'rosebud': rosebud, 'wavedash': wavedash}


def run(date=None, only=None):
    cfg = util.config()
    date = date or util.today()
    summary = {}
    for name, fn in COLLECTORS.items():
        if only and name not in only:
            continue
        try:
            leads = fn(cfg, date)
            err = ''
        except Exception as e:                      # 一个渠道挂了不影响其余渠道
            leads, err = [], f'{type(e).__name__}: {e}'
        util.save(util.p('raw', name, f'{date}.json'),
                  {'date': date, 'channel': name, 'count': len(leads),
                   'passed': sum(1 for l in leads if l['passes']), 'error': err, 'leads': leads})
        summary[name] = {'count': len(leads), 'passed': sum(1 for l in leads if l['passes']), 'error': err}
        print(f"[{name}] 线索 {len(leads)}，过门槛 {summary[name]['passed']}" + (f'，出错：{err}' if err else ''))
    return summary


if __name__ == '__main__':
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None, set(sys.argv[2:]) or None)
