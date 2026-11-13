# -*- coding: utf-8 -*-
"""线索的二次补充：读正文、找原始出处。采集阶段调用，结果写进线索本身。

1. read_article(url)：读文章正文，找文中给出的试玩链接和 AI 线索。HN 上很多游戏是以一篇文章的形式出现的，
   只看标题会把游戏当成文章拒掉（例：Fable 5.2 one-shotted my nephew's dream game，游戏链接在正文里）。
2. enrich_hn(leads, cfg)：HN 线索过了分数线后，文章页读正文；标题看不出是游戏的 Show HN 读页面标题和描述。
3. trace_origin(...)：二手渠道（合集站）上的游戏，链接可能是别人的镜像。按作者帖子 → 源码仓库 → HN 搜索的顺序
   找原始出处，取原始的试玩链接、发布日期和传播数字。
"""
import html
import re
from urllib.parse import quote, urljoin

from . import net, util
from .collectors import GAMEY, ai_hint
from .x_ingest import STATUS, _as_links, classify_link, fx_tweet, host_in, tweet_urls

PLAY_CTX = re.compile(r'\b(play|played|playing|playable|try|tried|demo|game is (?:live|up|here))\b|试玩|玩一下|在线玩', re.I)
STRICT_GAME = re.compile(r'\b(game|games|playable|roguelike|roguelite|shooter|platformer|rpg|mmo|arcade|tycoon|'
                         r'multiplayer battle|puzzle game|racing game|strategy game)\b|游戏', re.I)
REMAKE = re.compile(r'\b(recreat\w*|remake|remade|clone|reimagin\w*|inspired by|port of|ported)\b', re.I)
SKIP_HOSTS = ('news.ycombinator.com', 'twitter.com', 'x.com', 'facebook.com', 'linkedin.com', 'reddit.com',
              'youtube.com', 'youtu.be', 'mastodon.social', 'bsky.app', 'fable52.games', 'web.archive.org',
              'archive.org', 'wikipedia.org', 'google.com', 'apple.com', 'gravatar.com', 'getpelican.com')


def _text(h):
    h = re.sub(r'<(script|style|noscript|svg|nav|header|footer)[^>]*>.*?</\1>', ' ', h or '', flags=re.S | re.I)
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', h)).split())


def read_article(url, cfg, page=None):
    """读文章正文。返回 {'ok','game_url','anchor','ai_hint','excerpt'}。
    只认文中"play / try / demo"附近的外链，避免把参考链接当成游戏。"""
    h = page if page is not None else net.get(url, timeout=30, maxb=6_000_000)
    if not h:
        return {'ok': False}
    body = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', ' ', h, flags=re.S | re.I)
    m = re.search(r'<(article|main)[^>]*>(.*?)</\1>', body, re.S | re.I)
    main = m.group(2) if m else body
    own = util.root_domain(util.host_of(url))
    best = None
    for a in re.finditer(r'<a\s[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', main, re.S | re.I):
        href = urljoin(url, html.unescape(a.group(1)))
        host = util.host_of(href)
        if not href.startswith('http') or not host or util.root_domain(host) == own or host_in(host, SKIP_HOSTS):
            continue
        kind = classify_link(href, cfg)
        if kind not in ('game', 'site'):
            continue
        anchor = _text(a.group(2))
        before = _text(main[max(0, a.start() - 300):a.start()])[-80:]
        if not (PLAY_CTX.search(anchor) or PLAY_CTX.search(before)):
            continue
        best = best or {'game_url': href, 'anchor': anchor[:80], 'context': (before + ' [' + anchor + ']')[-160:]}
    text = _text(main)
    return {'ok': True, 'ai_hint': ai_hint(text[:20000]), 'excerpt': text[:400], **(best or {'game_url': ''})}


def enrich_hn(leads, cfg, page_meta=None):
    """对过了分数线的 HN 线索读正文。直接改动传入的线索。"""
    from .merge import page_meta as _pm
    page_meta = page_meta or _pm
    th = cfg['thresholds']['floors']['hn_points']
    for l in leads:
        if l['channel'] != 'hn' or (l['metrics'].get('hn_points') or 0) < th or l.get('read'):
            continue
        kind = classify_link(l['url'], cfg)
        if kind == 'article' and l.get('passes'):
            r = read_article(l['url'], cfg)
            l['read'] = 'article'
            if r.get('ok'):
                l['article'] = {k: r.get(k) for k in ('excerpt', 'context') if r.get(k)}
                l['ai_hint'] = l.get('ai_hint') or r.get('ai_hint') or ''
                if r.get('game_url'):
                    # 文章里给了试玩链接：线索改指向游戏，文章留作出处
                    l['article']['url'] = l['url']
                    l['url'], l['key'] = r['game_url'], util.game_key(r['game_url'])
                    l['matched'] = 'article_link'
        elif l.get('matched') == 'show_hn' and kind in ('site', 'game'):
            meta = page_meta(l['url'])
            l['read'] = 'page'
            if meta.get('ok'):
                l['desc'] = l.get('desc') or meta.get('desc', '')
                text = (meta.get('title') or '') + ' ' + (meta.get('desc') or '')
                # 页面自己说是游戏，或者标题说是在重做某款作品；两种都要求页面里有画布或游戏引擎
                if meta.get('has_canvas') and (STRICT_GAME.search(text) or REMAKE.search(l['title'])):
                    l['matched'], l['passes'] = 'page', True
                l['ai_hint'] = l.get('ai_hint') or ai_hint(text)
    return leads


def _gh_repo(repo_url):
    m = re.search(r'github\.com/([^/]+/[^/#?]+)', repo_url or '')
    if not m:
        return {}
    name = m.group(1)[:-4] if m.group(1).endswith('.git') else m.group(1)
    d = net.getj(f'https://api.github.com/repos/{name}', headers=net.github_headers(), timeout=25) or {}
    if not d.get('full_name'):
        return {}
    return {'url': d.get('html_url'), 'created': (d.get('created_at') or '')[:10], 'stars': d.get('stargazers_count'),
            'homepage': d.get('homepage') or '', 'desc': d.get('description') or '', 'owner': (d.get('owner') or {}).get('login')}


def _hn_search(title, since_ts):
    words = re.sub(r'[^\w\s]', ' ', title).strip()
    if len(words) < 3:
        return {}
    d = net.getj('https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=10&query=' + quote(words)
                 + '&numericFilters=' + quote(f'created_at_i>={since_ts}'), timeout=25) or {}
    best = {}
    for h in d.get('hits', []):
        t = (h.get('title') or '').lower()
        if words.lower() not in re.sub(r'[^\w\s]', ' ', t) and title.lower() not in t:
            continue
        # 单个词的名字（Daedalus 一类）容易撞上别的帖子：标题里还要有游戏或 Fable 一类的词
        if len(words.split()) < 2 and not re.search(r'\b(game|games|play|fable|claude|three\.?js|webgl)\b', t):
            continue
        if not best or (h.get('points') or 0) > best['points']:
            best = {'url': f"https://news.ycombinator.com/item?id={h['objectID']}", 'title': h.get('title'),
                    'points': h.get('points') or 0, 'comments': h.get('num_comments') or 0,
                    'date': (h.get('created_at') or '')[:10], 'link': h.get('url') or ''}
    return best


def trace_origin(title, play_url, author_url, repo_url, cfg, since):
    """二手渠道条目的原始出处。返回 {'url','how','mirror','published','x','repo','hn','ai_hint'}。"""
    from .collectors import _epoch
    out = {'x': {}, 'repo': {}, 'hn': {}}
    picks = []                                       # (来源说明, 试玩链接)
    m = STATUS.search(author_url or '')
    if m:
        tw = fx_tweet(m.group(1), m.group(2))
        if tw:
            out['x'] = {'url': f'https://x.com/{m.group(1)}/status/{m.group(2)}', 'handle': m.group(1),
                        'views': tw.get('views'), 'likes': tw.get('likes'), 'date': util_date(tw.get('created_timestamp')),
                        'text': (tw.get('text') or '')[:600]}
            for l in _as_links(dict.fromkeys(tweet_urls(tw)), cfg):
                if l['kind'] in ('game', 'site') and not host_in(util.host_of(l['final']), SKIP_HOSTS):
                    picks.append(('作者帖子里的链接', l['final']))
                    break
    out['repo'] = _gh_repo(repo_url)
    hp = out['repo'].get('homepage')
    if hp and classify_link(hp, cfg) in ('game', 'site') and not host_in(util.host_of(hp), SKIP_HOSTS):
        picks.append(('源码仓库的主页', hp))
    out['hn'] = _hn_search(title, _epoch(since))
    hl = out['hn'].get('link')
    if hl and classify_link(hl, cfg) in ('game', 'site') and not host_in(util.host_of(hl), SKIP_HOSTS):
        picks.append(('Hacker News 帖子的链接', hl))
    how, url = picks[0] if picks else ('合集站给的链接', play_url)
    out.update(url=url, how=how, mirror=util.game_key(url) != util.game_key(play_url))
    dates = [d for d in (out['x'].get('date'), out['repo'].get('created'), out['hn'].get('date')) if d]
    out['published'] = min(dates) if dates else ''
    out['ai_hint'] = ai_hint(out['x'].get('text'), out['repo'].get('desc'))
    return out


def util_date(ts):
    import datetime
    try:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return ''
