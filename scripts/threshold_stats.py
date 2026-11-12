# -*- coding: utf-8 -*-
"""给入库线和重点线找数据依据：扫各渠道近 30 天的数值分布。

  python3 scripts/threshold_stats.py hn       Hacker News 近 30 天全部帖子（按 6 小时分窗），取标题像在讲游戏的
  python3 scripts/threshold_stats.py github   GitHub 各搜索词在不同星数下的仓库数（近 30 天创建）
  python3 scripts/threshold_stats.py other    three.js 论坛、X 样例、各平台热门页（读本地已有的数据和论坛接口）
结果写到 backtest/threshold_stats.json（每个阶段各占一个键）。不改动 data/ 下的任何文件。
"""
import datetime
import os
import re
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitor import collectors, net, util  # noqa: E402

OUT = util.p('backtest', 'threshold_stats.json')
DAYS = 30


def save(key, val):
    d = util.load(OUT, {})
    d[key] = val
    util.save(OUT, d)


def hn(date):
    start = datetime.datetime.fromisoformat(collectors._shift(date, -DAYS)).replace(tzinfo=datetime.timezone.utc)
    end = datetime.datetime.fromisoformat(collectors._shift(date, 1)).replace(tzinfo=datetime.timezone.utc)
    # 第一步：全部帖子在各分数档的数量，直接用接口的计数，不逐条读
    win = f'created_at_i>={int(start.timestamp())},created_at_i<{int(end.timestamp())}'
    counts = {}
    for n in (0, 2, 5, 10, 30, 50, 100, 200, 300, 500):
        t0 = time.time()
        d = net.getj('https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=1&numericFilters='
                     + quote(win + (f',points>={n}' if n else '')), retries=2) or {}
        counts[str(n)] = d.get('nbHits')
        print('  全部帖子 ≥', n, '分：', d.get('nbHits'), f'（{time.time() - t0:.1f} 秒）', flush=True)
    save('hn_all', {'date': date, 'days': DAYS, 'counts': counts})
    # 第二步：≥ MIN 分的帖子逐条读，取标题像在讲游戏的。更低分的量太大，读不完
    MIN = 10
    seen, total, a = {}, 0, start
    while a < end:
        b = a + datetime.timedelta(hours=48)
        for page in range(10):
            u = ('https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=100'
                 f'&page={page}&numericFilters=' + quote(f'created_at_i>={int(a.timestamp())},created_at_i<{int(b.timestamp())},points>={MIN}'))
            hits = (net.getj(u, retries=2) or {}).get('hits', [])
            total += len(hits)
            for h in hits:
                t, url = h.get('title') or '', h.get('url') or ''
                if url and (collectors.GAMEY.search(t) or util.host_of(url).endswith('itch.io')):
                    seen[h['objectID']] = {'points': h.get('points') or 0, 'title': t, 'url': url,
                                           'date': collectors._date(ts=h.get('created_at_i')),
                                           'show': t.lower().startswith('show hn')}
            if len(hits) < 100:
                break
        a = b
        print(' ', a.date(), f'≥ {MIN} 分的帖子累计', total, '其中标题像游戏', len(seen), flush=True)
        save('hn', {'date': date, 'days': DAYS, 'min_points': MIN, 'scanned_until': str(a.date()),
                    'stories_scanned': total, 'gamey': list(seen.values())})
    print('hn done', total, len(seen))


def github(date):
    cfg = util.config()
    since = collectors._shift(date, -DAYS)
    res = ((util.load(OUT, {}).get('github') or {}).get('counts')) or {}    # 已经取到的不重取，只补缺的
    for q in cfg['github_queries']:
        res.setdefault(q, {})
        for n in (0, 1, 10, 30, 100, 300, 1000):
            if res[q].get(str(n)) is not None:
                continue
            u = ('https://api.github.com/search/repositories?per_page=1&q=' + quote(f'{q} created:{since}..{date} stars:>={n}'))
            d = net.getj(u, headers=net.github_headers(), retries=0) or {}
            if 'rate limit' in str(d.get('message', '')):
                print('  撞到频率限制，等 65 秒', flush=True)
                time.sleep(65)
                d = net.getj(u, headers=net.github_headers(), retries=0) or {}
            res[q][str(n)] = d.get('total_count')
            print(' ', q, n, d.get('total_count'), d.get('message', '')[:60], flush=True)
            time.sleep(7 if 'Authorization' not in net.github_headers() else 1)
        save('github', {'date': date, 'days': DAYS, 'since': since, 'counts': res})
    print('github done')


def other(date):
    # three.js 论坛作品区：近 30 天创建的帖子的浏览数（到今天的累计值）
    since = collectors._shift(date, -DAYS)
    topics = {}
    for period in ('monthly', 'weekly'):
        for page in range(0, 12):
            d = net.getj(f'https://discourse.threejs.org/c/showcase/7/l/top.json?period={period}&page={page}', retries=2) or {}
            ts = (d.get('topic_list') or {}).get('topics', [])
            if not ts:
                break
            for t in ts:
                if (t.get('created_at') or '')[:10] >= since:
                    topics[t['id']] = {'views': t.get('views') or 0, 'title': t.get('title') or '',
                                       'date': t['created_at'][:10], 'gamey': bool(collectors.GAMEY.search(t.get('title') or ''))}
            time.sleep(0.2)
    save('threejs', {'date': date, 'days': DAYS, 'topics': list(topics.values())})
    print('threejs', len(topics))
    # X 样例：全部帖子、粗筛命中、判断为游戏或待定的浏览数
    xs = {'all': [], 'coarse': [], 'game': []}
    for path in sorted(__import__('glob').glob(util.p('inbox', 'x', '*.json'))):
        for r in (util.load(path) or {}).get('records', []):
            xs['all'].append(r.get('views') or 0)
    judg = util.load(util.p('data', 'x_judgments.json'), {})
    from monitor import x_ingest
    for path in sorted(__import__('glob').glob(util.p('raw', 'x', '*.json'))):
        for it in (util.load(path) or {}).get('items', []):
            v = (it.get('game') or {}).get('views_now') or it.get('views') or 0
            xs['coarse'].append(v)
            j = judg.get(it['tweet_id'])
            verdict, _ = x_ingest.judge(it, j)
            if verdict == 'game' or (verdict == 'maybe' and x_ingest.linkless_ok(it, j)):
                xs['game'].append({'views': v, 'handle': it['handle']})
    save('x', xs)
    # 各平台热门页：今天榜上的游玩数
    plats = {}
    for ch, key in (('spawn', 'spawn_plays'), ('astrocade', 'astrocade_plays'), ('rosebud', 'rosebud_plays'),
                    ('verse8', 'verse8_sessions'), ('wavedash', 'wavedash_playing')):
        raw = util.load(util.p('raw', ch, f'{date}.json')) or {}
        plats[ch] = [{'v': l['metrics'].get(key) or 0, 'title': l['title'], 'published': l.get('published') or '',
                      'passes': l.get('passes')} for l in raw.get('leads', [])]
    save('platforms', plats)
    print('other done')


if __name__ == '__main__':
    what = sys.argv[1]
    date = sys.argv[2] if len(sys.argv) > 2 else util.today()
    {'hn': hn, 'github': github, 'other': other}[what](date)
