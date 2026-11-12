# -*- coding: utf-8 -*-
"""回测：用 2026-04-01 到 2026-11-10 的历史数据重放各渠道，看基线的 68 款能被哪些渠道发现。

做法：
  Hacker News   按月重放采集器（同样的关键词、同样的"标题像游戏"过滤），再按游戏域名单独查一次，
                用来区分"有帖但关键词没覆盖"和"根本没上过 HN"
  three.js 论坛  读年度热门帖，对浏览 ≥ 500 的帖子取首帖外链
  GitHub        按月重放采集器的搜索词（星数取现在的值，不是创建 30 天内的值）
  精选清单       读 awesome-ai-built-games 的全部条目
  Spawn         读当前热榜
  Verse8、Astrocade、Rosebud、Wavedash  读当前的热门页（和采集器同一套读取逻辑，不写入采集状态）
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
            if key == g['key'] or key in g.get('alt_keys', []) or \
                    ('/' not in g['key'] and (host == util.host_of(g['url']))):
                out.append(g['id'])
        return out

    # ---- Hacker News：重放采集器 ----
    print('HN 重放…', flush=True)
    for a, b in MONTHS:
        if 'hn-' + a in part['done']:
            continue
        for l in collectors.hn(cfg, b, since=a, min_points=50):
            for gid in match(l['key'], l['url']):
                h = res[gid]['hits'].setdefault('hn', {'points': 0})
                if l['metrics']['hn_points'] > h['points']:
                    h.update(points=l['metrics']['hn_points'], date=l['published'], url=l['source_url'], title=l['title'])
        checkpoint('hn-' + a)
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
    seen = 0
    for page in ([] if 'threejs' in part['done'] else range(0, 14)):
        d = net.getj(f'https://discourse.threejs.org/c/showcase/7/l/top.json?period=yearly&page={page}', retries=2) or {}
        topics = (d.get('topic_list') or {}).get('topics', [])
        if not topics:
            break
        for t in topics:
            if (t.get('created_at') or '')[:10] < START or (t.get('views') or 0) < 200:
                continue
            seen += 1
            td = net.getj(f"https://discourse.threejs.org/t/{t['id']}.json") or {}
            p0 = ((td.get('post_stream') or {}).get('posts') or [{}])[0]
            for l in p0.get('link_counts') or []:
                for gid in match(util.game_key(l['url']), l['url']):
                    if res[gid]['hits'].get('threejs', {}).get('views', 0) >= t['views']:
                        continue
                    res[gid]['hits']['threejs'] = {'views': t['views'], 'date': t['created_at'][:10],
                                                   'url': f"https://discourse.threejs.org/t/{t['slug']}/{t['id']}"}
            time.sleep(0.1)
    print('  浏览 ≥ 200 的帖子', seen, flush=True)
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

    # ---- 四个平台的热门页：借用采集器的读取逻辑，但不写入采集状态 ----
    print('平台热门页…')
    keep = collectors._chart
    collectors._chart = lambda name, entries, cfg_, date_: entries
    try:
        for name in ('verse8', 'astrocade', 'rosebud', 'wavedash'):
            try:
                entries = collectors.COLLECTORS[name](cfg, util.today())
            except Exception as ex:
                print('  ', name, '读取失败', ex)
                continue
            for l in entries:
                for gid in match(l['key'], l['url']):
                    m = l['metrics']
                    res[gid]['hits'][name] = {'rank': m.get(f'{name}_rank'), 'plays': m.get(f'{name}_plays') or m.get('verse8_sessions'),
                                              'playing': m.get('wavedash_playing')}
    finally:
        collectors._chart = keep

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


# 个案说明：依据是基线清单里各款的"数据来源"和首发渠道分析
CASES = {
    'tidewater': '首发在 X（首发帖 90 万浏览）；GitHub 仓库现有 1,447 星，但采集用的搜索词没有命中它',
    'bf1942': '首发在开发者博客和 X；Hacker News 上只有 6 分',
    'pokebonk': '首发在 X（首发帖 109 万浏览），固定渠道里没有出现',
    'lagoslife': '首发在 X（首发帖 607 万浏览），固定渠道里没有出现',
    'messenger': '2025-09 发布的老游戏（当时 Hacker News 2,131 分，在回测窗口之前），2026-06 靠媒体报道再度走红；媒体渠道还没有采集器',
    'farworlds': '来自平台梳理（平台自研的产品），不是渠道线索',
}
X_FIRST = ('tidewater', 'bf1942', 'pokebonk', 'lagoslife', 'messenger')
PLAT = {'verse8': 'Verse8 热门', 'astrocade': 'Astrocade 热门页', 'rosebud': 'Rosebud 首页精选', 'wavedash': 'Wavedash 热门'}


def report(res, cfg):
    F = cfg['thresholds']['floors']
    low = (F.get('chart_min_plays') or {}).get('astrocade', 0)
    rows, found, passed = [], 0, 0
    miss_reason = {}
    for gid, r in res.items():
        h = r['hits']
        tags, ok = [], False
        # Hacker News：重放命中的直接算；只按域名查到的，标题要像在讲游戏才算（采集器靠标题过滤）
        hn_pts, hn_ok = 0, False
        if 'hn' in h:
            hn_pts, hn_ok = h['hn']['points'], True
        d = h.get('hn_direct')
        if d and d['date'] >= START and (d['points'] or 0) > hn_pts:
            hn_pts = d['points'] or 0
            hn_ok = bool(collectors.GAMEY.search(d.get('title') or ''))
        if hn_pts:
            p = hn_ok and hn_pts >= F['hn_points']
            ok |= p
            tags.append(f"Hacker News {hn_pts} 分" + ('' if p else f"（入库线 {F['hn_points']} 分）" if hn_pts < F['hn_points']
                                                      else '（标题里没有游戏词，采集器滤不到）'))
        h['_hn_pass'] = bool(hn_pts and hn_ok and hn_pts >= F['hn_points'])
        if 'threejs' in h:
            p = h['threejs']['views'] >= F['threejs_views_week']
            ok |= p
            tags.append(f"three.js 论坛 {h['threejs']['views']} 次浏览")
        if 'github' in h:
            p = h['github']['stars'] >= F['github_stars_30d']
            ok |= p
            tags.append(f"GitHub {h['github']['stars']} 星")
        if 'awesome' in h:
            ok = True
            tags.append('精选清单')
        if 'spawn' in h:
            ok = True
            tags.append(f"Spawn 热榜第 {h['spawn']['rank']} 名")
        for name, lab in PLAT.items():
            if name in h:
                p = not (name == 'astrocade' and (h[name].get('plays') or 0) < low)
                ok |= p
                tags.append(lab + '在榜')
        if 'x' in h:
            ok = True
            tags.append(f"X 样例 {len(h['x']['posts'])} 条")
        r['tags'], r['found'], r['passed'] = tags, bool(tags), ok
        found += bool(tags)
        passed += ok
        r.pop('miss', None)
        if not ok:
            g = r['group']
            if gid in X_FIRST:
                why, bucket = CASES[gid], 'X 或媒体首发，固定渠道没有覆盖'
            elif gid in CASES:
                why, bucket = CASES[gid], '来自平台梳理，不是渠道线索'
            elif g in ('Verse8', 'Astrocade', 'Rosebud AI', 'Wavedash'):
                why = bucket = '平台游戏，但现在已经不在热门页上'
            elif tags:
                why = bucket = '到过渠道，但没到入库线'
            else:
                why = bucket = '固定渠道里没有出现'
            r['miss'] = why
            miss_reason.setdefault(bucket, []).append(r['name'])
        rows.append(r)
    lines = ['# 回测报告：68 款基线游戏的渠道召回', '',
             f'- 回测窗口：2026-04-01 至 2026-11-10。运行日期：{util.today()}。按 2026-11-12 放宽后的入库线计算。',
             f'- 68 款里，在已接入的渠道出现过的有 **{found}** 款；按现行入库线能被发现的有 **{passed}** 款，漏掉 **{68 - passed}** 款。',
             '- 口径：Hacker News 和 GitHub 是按月重放采集器；three.js 论坛用年度热门帖；精选清单、Spawn 热榜和四个平台的热门页用当前内容；'
             'X 只有两天样例。',
             '- 这是召回的上限，不是当时真能抓到的数量：GitHub 星数和论坛浏览数是现在的累计值；'
             '精选清单和各平台热门页只能看到现在的内容，"现在在榜"不等于"当时一上榜就能抓到"。',
             '- GitHub 现在还要求仓库有试玩主页、并且不是引擎或框架，这一条回测里没有重放。', '',
             '## 按渠道', '', '| 渠道 | 过入库线的款数 | 游戏 |', '|---|---|---|']

    def names(pred):
        return [r['name'] for r in rows if pred(r['hits'])]
    chans = [
        (f"Hacker News（≥ {F['hn_points']} 分）", lambda h: h.get('_hn_pass')),
        (f"three.js 论坛（≥ {F['threejs_views_week']} 次浏览）", lambda h: 'threejs' in h and h['threejs']['views'] >= F['threejs_views_week']),
        (f"GitHub（≥ {F['github_stars_30d']} 星）", lambda h: 'github' in h and h['github']['stars'] >= F['github_stars_30d']),
        ('AI 游戏精选清单', lambda h: 'awesome' in h),
        ('Spawn 热榜', lambda h: 'spawn' in h)]
    chans += [(lab, (lambda n: lambda h: n in h)(name)) for name, lab in PLAT.items()]
    chans.append(('X 样例（两天）', lambda h: 'x' in h))
    for lab, pred in chans:
        ns = names(pred)
        lines.append(f'| {lab} | {len(ns)} | {"、".join(ns)} |')
    lines += ['', '## 漏掉的游戏和原因', '', '| 原因 | 款数 | 游戏 |', '|---|---|---|']
    for why, ns in sorted(miss_reason.items(), key=lambda kv: -len(kv[1])):
        lines.append(f'| {why} | {len(ns)} | {"、".join(ns)} |')
    lines += ['', '## 逐款明细', '', '| 游戏 | 方向/平台 | 能否被发现 | 出现的渠道 | 漏掉的原因 |', '|---|---|---|---|---|']
    for r in rows:
        lines.append(f"| {r['name']} | {r['group']} | {'是' if r['passed'] else '否'} | {'；'.join(r['tags']) or '—'} | {r.get('miss', '')} |")
    with open(util.p('backtest', '回测报告.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    util.save(util.p('backtest', 'result.json'), res)
    print(f'found {found}/68, passed {passed}/68')
    for why, ns in miss_reason.items():
        print(' ', len(ns), why, '：', '、'.join(ns))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'report':
        report(util.load(util.p('backtest', 'result.json')), util.config())
    else:
        main()
