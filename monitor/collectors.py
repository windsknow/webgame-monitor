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


def threshold(cfg, name, date):
    """门槛值。新模型发布后的一段时间里，传播类门槛按配置打折。"""
    v = cfg['thresholds'][name]
    b = cfg.get('model_release_boost') or {}
    for r in b.get('releases', []):
        if 0 <= util.days_between(r['date'], date) <= b.get('days', 14):
            return v * b.get('factor', 0.5)
    return v


# ---------------- Hacker News ----------------
def hn(cfg, date, since=None, min_points=30, queries=None):
    since = since or _shift(date, -cfg['window']['reheat_days'])
    th = threshold(cfg, 'hn_points_7d', date)
    seen, leads = {}, []
    for q in (queries or cfg['hn_queries']):
        for page in range(0, 3):
            u = ('https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=100'
                 f'&page={page}&query={quote(q)}'
                 '&numericFilters=' + quote(f'created_at_i>{_epoch(since)},created_at_i<{_epoch(_shift(date, 1))},points>={min_points}'))
            d = net.getj(u, retries=2) or {}
            hits = d.get('hits', [])
            for h in hits:
                seen[h['objectID']] = h
            if len(hits) < 100:
                break
    for h in seen.values():
        url, title = h.get('url'), h.get('title') or ''
        if not url:
            continue
        host = util.host_of(url)
        gamey = bool(GAMEY.search(title))
        if not gamey and not any(host.endswith(s) for s in ('itch.io',)):
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
    th = threshold(cfg, 'threejs_views_week', date)
    d = net.getj(f'https://discourse.threejs.org/c/showcase/7/l/top.json?period={period}', retries=2) or {}
    leads = []
    for t in (d.get('topic_list') or {}).get('topics', []):
        passes = (t.get('views') or 0) >= th
        url, hint = '', ''
        if fetch_links and (passes or (t.get('views') or 0) >= th / 2):
            td = net.getj(f"https://discourse.threejs.org/t/{t['id']}.json") or {}
            posts = (td.get('post_stream') or {}).get('posts') or [{}]
            p0 = posts[0]
            ext = [l['url'] for l in (p0.get('link_counts') or []) if not l.get('internal')
                   and not re.search(r'\.(png|jpe?g|gif|webp|mp4)(\?|$)', l['url'])]
            pref = [u for u in ext if util.host_of(u) not in ('github.com', 'youtube.com', 'youtu.be', 'x.com', 'twitter.com')]
            url = (pref or ext or [''])[0]
            hint = ai_hint(t.get('title'), re.sub(r'<[^>]+>', ' ', p0.get('cooked') or ''))
            time.sleep(0.3)
        leads.append({
            'channel': 'threejs', 'title': t.get('title') or '', 'url': url, 'key': util.game_key(url),
            'source_url': f"https://discourse.threejs.org/t/{t.get('slug')}/{t['id']}",
            'metrics': {'threejs_views': t.get('views'), 'threejs_replies': max((t.get('posts_count') or 1) - 1, 0),
                        'threejs_likes': t.get('like_count')},
            'published': _date(iso=t.get('created_at')), 'passes': passes, 'ai_hint': hint, 'desc': '',
        })
    return leads


# ---------------- GitHub ----------------
def github(cfg, date, since=None, min_stars=None):
    since = since or _shift(date, -cfg['window']['new_days'])
    th = cfg['thresholds']['github_stars_30d']
    min_stars = min_stars or max(int(th / 2), 20)
    seen = {}
    for q in cfg['github_queries']:
        u = ('https://api.github.com/search/repositories?sort=stars&order=desc&per_page=50&q='
             + quote(f'{q} created:{since}..{date} stars:>={min_stars}'))
        d = net.getj(u, headers=net.github_headers(), retries=1) or {}
        for r in d.get('items', []) or []:
            seen[r['full_name']] = r
        time.sleep(7 if 'Authorization' not in net.github_headers() else 1)   # 匿名每分钟 10 次
    leads = []
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
            'published': _date(iso=r.get('created_at')), 'passes': (r.get('stargazers_count') or 0) >= th,
            'ai_hint': ai_hint(text), 'desc': r.get('description') or '',
        })
    return leads


# ---------------- itch.io 网页平台新游 ----------------
def itch(cfg, date):
    """读"新品与热门·网页平台"榜的 RSS，并累计每款游戏在榜的日期。在榜跨度满两周才过门槛。"""
    xml = net.get('https://itch.io/games/new-and-popular/platform-web.xml', retries=2)
    state_p = util.p('data', 'state', 'itch_chart.json')
    state = util.load(state_p, {})
    leads = []
    weeks = cfg['thresholds']['itch_weeks_on_chart']
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
            'passes': span >= (weeks - 1) * 7 and len(s['dates']) >= 2,
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


# ---------------- Spawn 热榜 ----------------
def spawn(cfg, date):
    """读 Spawn 热榜，和上次的榜单对比：新进前 N 名的、榜首换人的，才算过门槛。首次运行只建立基线。"""
    d = net.getj('https://www.spawn.co/api/feed?sort=top', retries=2) or {}
    state_p = util.p('data', 'state', 'spawn_top.json')
    state = util.load(state_p, None)
    first_run = state is None
    prev = set((state or {}).get('keys', []))
    prev_top = (state or {}).get('top1')
    n = cfg['thresholds']['spawn_top_n']
    leads, keys = [], []
    for rank, g in enumerate(d.get('games', [])[:n], 1):
        owner = (g.get('profiles') or {}).get('username') or ''
        gm = g.get('games') or {}
        slug = gm.get('adventure_name') or ''
        if not owner or not slug:
            continue
        url = f'https://www.spawn.co/@{owner}/{slug}'
        k = util.game_key(url)
        keys.append(k)
        new_entry = (k not in prev) and not first_run
        new_top = rank == 1 and prev_top and prev_top != k
        leads.append({
            'channel': 'spawn', 'title': gm.get('name') or slug, 'url': url, 'key': k,
            'source_url': 'https://www.spawn.co/',
            'metrics': {'spawn_rank': rank, 'spawn_plays': g.get('play_count'), 'spawn_likes': g.get('like_count')},
            'published': _date(iso=g.get('posted_at')),
            'passes': bool(new_entry or new_top),
            'ai_hint': '发布在 AI 创作平台 Spawn', 'desc': ((g.get('box_copy') or {}).get('blurb') or '')[:300],
            'image': g.get('og_image_url') or g.get('custom_thumbnail_url') or '',
        })
    if keys:
        util.save(state_p, {'date': date, 'keys': keys, 'top1': keys[0]})
    return leads


COLLECTORS = {'hn': hn, 'threejs': threejs, 'github': github, 'itch': itch, 'awesome': awesome, 'spawn': spawn}


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
