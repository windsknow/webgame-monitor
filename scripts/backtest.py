# -*- coding: utf-8 -*-
"""回测：用 2026-04-01 到 2026-11-10 的历史数据重放各渠道，看基线的 68 款能被哪些渠道发现。

做法：
  Hacker News   按月重放采集器（同样的关键词、同样的"标题像游戏"过滤），再按游戏域名单独查一次，
                用来区分"有帖但关键词没覆盖"和"根本没上过 HN"
  three.js 论坛  读年度热门帖，对浏览 ≥ 500 的帖子取首帖外链
  GitHub        按月重放采集器的搜索词（星数取现在的值，不是创建 30 天内的值）
  精选清单       读 awesome-ai-built-games 的全部条目
  Spawn         读当前热榜
  X             重放两份样例；其余游戏只能看开发者账号是否出现在样例的作者里
结果写到 backtest/result.json 和 backtest/回测报告.md。不改动 data/ 下的任何文件。
"""
import calendar
import os
import re
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitor import collectors, net, util  # noqa: E402

START, END = '2026-04-01', '2026-11-10'
MONTHS = [(f'2026-{m:02d}-01', min(f'2026-{m:02d}-{calendar.monthrange(2026, m)[1]:02d}', END)) for m in range(4, 12)]


def keyset(g):
    ks = {g['key']}
    host = util.host_of(g['url'])
    if '/' not in g['key']:
        ks.add(util.root_domain(host))
    return ks


def main():
    cfg = util.config()
    lib = [g for g in util.load(util.p('data', 'games.json')) if g.get('issue') == '基线']
    res = {g['id']: {'name': g['name'], 'group': g['group'], 'url': g['url'], 'date_text': g.get('date_text', ''),
                     'hits': {}} for g in lib}
    part = util.load(util.p('backtest', 'partial.json'), {'done': [], 'hits': {}})
    for gid, h in part['hits'].items():
        if gid in res:
            res[gid]['hits'] = h

    def checkpoint(phase):
        part['done'].append(phase)
        part['hits'] = {gid: r['hits'] for gid, r in res.items()}
        util.save(util.p('backtest', 'partial.json'), part)
        print('  完成', phase, flush=True)

    def match(key, url=''):
        host = util.host_of(url) if url else key.split('/')[0]
        out = []
        for g in lib:
            if key == g['key'] or ('/' not in g['key'] and (host == util.host_of(g['url']))):
                out.append(g['id'])
        return out

    # ---- Hacker News：重放采集器 ----
    print('HN 重放…', flush=True)
    for a, b in ([] if 'hn' in part['done'] else MONTHS):
        for l in collectors.hn(cfg, b, since=a, min_points=30):
            for gid in match(l['key'], l['url']):
                h = res[gid]['hits'].setdefault('hn', {'points': 0})
                if l['metrics']['hn_points'] > h['points']:
                    h.update(points=l['metrics']['hn_points'], date=l['published'], url=l['source_url'], title=l['title'])
    if 'hn' not in part['done']:
        checkpoint('hn')
    # ---- Hacker News：按域名直接查（不经过关键词） ----
    print('HN 按域名查…', flush=True)
    for g in ([] if 'hn_direct' in part['done'] else lib):
        host = util.host_of(g['url'])
        if host in ('github.com', 'claude.ai', 'spawn.co', 'verse8.io', 'astrocade.com', 'rosebud.ai', 'wavedash.com', 'levels.io'):
            q = g['key'].split('/', 1)[1] if '/' in g['key'] else host
        else:
            q = host
        d = net.getj('https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=20&restrictSearchableAttributes=url&query='
                     + quote(q), retries=1) or {}
        best = None
        for h in d.get('hits', []):
            if h.get('url') and match(util.game_key(h['url']), h['url']) and g['id'] in match(util.game_key(h['url']), h['url']):
                if not best or (h.get('points') or 0) > (best.get('points') or 0):
                    best = h
        if best:
            res[g['id']]['hits']['hn_direct'] = {'points': best.get('points'), 'date': collectors._date(ts=best.get('created_at_i')),
                                                 'title': best.get('title'),
                                                 'url': f"https://news.ycombinator.com/item?id={best['objectID']}"}
        time.sleep(0.15)

    if 'hn_direct' not in part['done']:
        checkpoint('hn_direct')
    # ---- three.js 论坛：年度热门 ----
    print('three.js 论坛…', flush=True)
    th = cfg['thresholds']['threejs_views_week']
    seen = 0
    for page in ([] if 'threejs' in part['done'] else range(0, 8)):
        d = net.getj(f'https://discourse.threejs.org/c/showcase/7/l/top.json?period=yearly&page={page}', retries=2) or {}
        topics = (d.get('topic_list') or {}).get('topics', [])
        if not topics:
            break
        for t in topics:
            if (t.get('created_at') or '')[:10] < START or (t.get('views') or 0) < th:
                continue
            seen += 1
            td = net.getj(f"https://discourse.threejs.org/t/{t['id']}.json") or {}
            p0 = ((td.get('post_stream') or {}).get('posts') or [{}])[0]
            for l in p0.get('link_counts') or []:
                for gid in match(util.game_key(l['url']), l['url']):
                    res[gid]['hits']['threejs'] = {'views': t['views'], 'date': t['created_at'][:10],
                                                   'url': f"https://discourse.threejs.org/t/{t['slug']}/{t['id']}"}
            time.sleep(0.1)
    print('  浏览 ≥ 500 的帖子', seen, flush=True)
    if 'threejs' not in part['done']:
        checkpoint('threejs')

    # ---- GitHub：重放搜索词（每个月存一次盘） ----
    print('GitHub 重放…', flush=True)
    for a, b in MONTHS:
        if 'github-' + a in part['done']:
            continue
        for l in collectors.github(cfg, b, since=a, min_stars=50):
            ids = set(match(l['key'], l['url']))
            for k in l.get('alt_keys', []):
                ids |= set(match(k))
            for gid in ids:
                res[gid]['hits']['github'] = {'stars': l['metrics']['github_stars'], 'date': l['published'], 'url': l['source_url']}
        checkpoint('github-' + a)

    # ---- 精选清单 ----
    print('精选清单…')
    md = net.get('https://raw.githubusercontent.com/lappemic/awesome-ai-built-games/HEAD/README.md', retries=2)
    for m in re.finditer(r'^- \[([^\]]+)\]\((https?://[^)\s]+)\)', md, re.M):
        for gid in match(util.game_key(m.group(2)), m.group(2)):
            res[gid]['hits']['awesome'] = {'name': m.group(1)}

    # ---- Spawn 热榜 ----
    print('Spawn…')
    d = net.getj('https://www.spawn.co/api/feed?sort=top', retries=2) or {}
    for rank, g in enumerate(d.get('games', []), 1):
        owner = (g.get('profiles') or {}).get('username') or ''
        slug = (g.get('games') or {}).get('adventure_name') or ''
        for gid in match(util.game_key(f'https://www.spawn.co/@{owner}/{slug}')):
            res[gid]['hits']['spawn'] = {'rank': rank, 'plays': g.get('play_count')}

    # ---- X 样例 ----
    print('X 样例…')
    authors = set()
    for f in ('2026-11-10', '2026-11-11'):
        raw = util.load(util.p('inbox', 'x', f + '.json')) or {}
        for r in raw.get('records', []):
            m = re.search(r'x\.com/([^/]+)/status', r.get('link') or '')
            if m:
                authors.add(m.group(1).lower())
        out = util.load(util.p('raw', 'x', f + '.json')) or {}
        for it in out.get('items', []):
            for gm in it.get('games', []):
                if gm.get('library') in res:
                    res[gm['library']]['hits'].setdefault('x', {'posts': []})['posts'].append(it['link'])
    for g in lib:
        hs = [h for h in g.get('x_handles', []) if h.lower() in authors]
        if hs:
            res[g['id']]['x_author_in_feed'] = hs

    util.save(util.p('backtest', 'result.json'), res)
    report(res, cfg)


def report(res, cfg):
    th = cfg['thresholds']
    rows, found, passed = [], 0, 0
    miss_reason = {}
    for gid, r in res.items():
        h = r['hits']
        tags, ok = [], False
        if 'hn' in h:
            p = h['hn']['points'] >= th['hn_points_7d']
            ok |= p
            tags.append(f"HN {h['hn']['points']} 分{'' if p else '（未到 100）'}")
        elif 'hn_direct' in h:
            tags.append(f"HN 有帖 {h['hn_direct']['points']} 分，但采集关键词没覆盖")
        if 'threejs' in h:
            ok = True
            tags.append(f"three.js 论坛 {h['threejs']['views']} 次浏览")
        if 'github' in h:
            p = h['github']['stars'] >= th['github_stars_30d']
            ok |= p
            tags.append(f"GitHub {h['github']['stars']} 星{'' if p else '（未到 100）'}")
        if 'awesome' in h:
            ok = True
            tags.append('精选清单')
        if 'spawn' in h:
            ok = True
            tags.append(f"Spawn 热榜第 {h['spawn']['rank']} 名")
        if 'x' in h:
            ok = True
            tags.append(f"X 样例 {len(h['x']['posts'])} 条")
        r['found'], r['passed'] = bool(tags), ok
        found += bool(tags)
        passed += ok
        if not ok:
            g = r['group']
            host = util.host_of(r['url'])
            if g in ('Verse8', 'Astrocade', 'Rosebud AI'):
                why = f'{g} 热门页还没有采集器（只读过页面，接口未找到）'
            elif g.startswith('Wavedash') or host == 'wavedash.com':
                why = 'Wavedash 热门页还没有采集器'
            elif 'Vibe Jam' in g or 'Vibe Jam' in r.get('date_text', ''):
                why = '赛事结果渠道（Vibe Jam、JamVault）还没有采集器'
            elif tags:
                why = '渠道里出现过，但没到门槛'
            else:
                why = '首发在 X 或媒体，固定渠道里没有出现；要靠 X 数据覆盖到开发者账号'
            r['miss'] = why
            miss_reason.setdefault(why, []).append(r['name'])
        rows.append(r)
    lines = ['# 回测报告：68 款基线游戏的渠道召回', '',
             f'- 回测窗口：2026-04-01 至 2026-11-10。运行日期：{util.today()}。',
             f'- 68 款里，至少在一个已接入的渠道出现过的有 **{found}** 款；按现行门槛能进候选的有 **{passed}** 款。',
             '- 口径：Hacker News 和 GitHub 是按月重放采集器；three.js 论坛用年度热门帖；精选清单和 Spawn 热榜用当前内容；'
             'X 只有两天样例。GitHub 星数是现在的值，不是创建 30 天内的值，所以 GitHub 的召回是上限。', '',
             '## 按渠道', '']
    ch = {'hn': 'Hacker News（采集器重放）', 'threejs': 'three.js 论坛', 'github': 'GitHub', 'awesome': 'AI 游戏精选清单',
          'spawn': 'Spawn 热榜', 'x': 'X 样例（两天）'}
    lines += ['| 渠道 | 命中款数 | 游戏 |', '|---|---|---|']
    for k, lab in ch.items():
        names = [r['name'] for r in rows if k in r['hits']]
        lines.append(f'| {lab} | {len(names)} | {"、".join(names)} |')
    hd = [r for r in rows if 'hn_direct' in r['hits'] and 'hn' not in r['hits']]
    lines.append(f'| Hacker News 有帖但关键词没覆盖 | {len(hd)} | '
                 + '、'.join(f"{r['name']}（{r['hits']['hn_direct']['points']} 分）" for r in hd) + ' |')
    lines += ['', '## 漏掉的游戏和原因', '', '| 原因 | 款数 | 游戏 |', '|---|---|---|']
    for why, names in sorted(miss_reason.items(), key=lambda kv: -len(kv[1])):
        lines.append(f'| {why} | {len(names)} | {"、".join(names)} |')
    lines += ['', '## 逐款明细', '', '| 游戏 | 方向/平台 | 能否进候选 | 出现的渠道 | 漏掉的原因 |', '|---|---|---|---|---|']
    for r in rows:
        h = r['hits']
        bits = []
        for k in ('hn', 'hn_direct', 'threejs', 'github', 'awesome', 'spawn', 'x'):
            if k in h:
                v = h[k]
                bits.append({'hn': lambda: f"HN {v['points']} 分（{v['date']}）",
                             'hn_direct': lambda: f"HN 按域名查到 {v['points']} 分（{v['date']}）",
                             'threejs': lambda: f"three.js {v['views']} 次浏览",
                             'github': lambda: f"GitHub {v['stars']} 星",
                             'awesome': lambda: '精选清单', 'spawn': lambda: f"Spawn 第 {v['rank']} 名",
                             'x': lambda: f"X 样例 {len(v['posts'])} 条"}[k]())
        lines.append(f"| {r['name']} | {r['group']} | {'是' if r['passed'] else '否'} | {'；'.join(bits) or '—'} | {r.get('miss', '')} |")
    with open(util.p('backtest', '回测报告.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    util.save(util.p('backtest', 'result.json'), res)
    print(f'found {found}/68, passed {passed}/68')
    for why, names in miss_reason.items():
        print(' ', len(names), why)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'report':
        report(util.load(util.p('backtest', 'result.json')), util.config())
    else:
        main()
