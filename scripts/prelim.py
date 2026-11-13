# -*- coding: utf-8 -*-
"""采集数据的初步处理：只做盘点、去重和分类，不联网，不入库，不改 data/ 下的任何文件。

  python3 scripts/prelim.py [日期，默认今天]

输出 reports/初步处理-<日期>.md：
  1. 各渠道采到多少、有没有出错、和之前相比新出现多少
  2. 跨渠道去重后有多少条，哪些同时出现在多个渠道
  3. 链接类型分布（游戏平台、独立站点、代码仓库、文章……）
  4. 按现行入库线过线的线索清单（标出今天新出现的、已在收录库里的）
  5. 没过线但数字靠前的线索，供定筛选标准时参考
  6. X 帖子的粗筛和判断结果
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitor import util, x_ingest  # noqa: E402

CH = ['hn', 'threejs', 'github', 'itch', 'awesome', 'spawn', 'verse8', 'astrocade', 'rosebud', 'wavedash', 'fable52']
CH_NAME = {'hn': 'Hacker News', 'threejs': 'three.js 论坛', 'github': 'GitHub', 'itch': 'itch.io', 'awesome': 'AI 游戏精选清单',
           'spawn': 'Spawn', 'verse8': 'Verse8', 'astrocade': 'Astrocade', 'rosebud': 'Rosebud', 'wavedash': 'Wavedash', 'fable52': 'Fable52.games', 'x': 'X 帖子'}
KIND = {'game': '游戏平台或托管站', 'site': '独立站点', 'repo': '代码仓库', 'article': '文章页', 'video': '视频',
        'social': '社交平台', 'store': '商店页', 'docs': '其他（文档、首页等）', '': '没有链接'}


def metric(l):
    m, ch = l.get('metrics') or {}, l['channel']
    if ch == 'hn':
        return m.get('hn_points') or 0, f"{m.get('hn_points')} 分 / {m.get('hn_comments') or 0} 评论"
    if ch == 'threejs':
        return m.get('threejs_views') or 0, f"{util.fmt_num(m.get('threejs_views'))} 次浏览"
    if ch == 'github':
        return m.get('github_stars') or 0, f"{util.fmt_num(m.get('github_stars'))} 星"
    if ch == 'itch':
        return -(m.get('itch_rank') or 999), f"榜单第 {m.get('itch_rank')} 名，在榜 {m.get('itch_days_on_chart')} 天"
    if ch == 'verse8':
        return m.get('verse8_sessions') or 0, f"游玩 {util.fmt_num(m.get('verse8_sessions'))} 次（口径存疑）"
    if ch == 'wavedash':
        return m.get('wavedash_playing') or 0, f"实时在线 {m.get('wavedash_playing')} 人"
    for k in ('spawn_plays', 'astrocade_plays', 'rosebud_plays', 'fable52_plays'):
        if k in m:
            return m.get(k) or 0, f"游玩 {util.fmt_num(m.get(k))} 次"
    return 0, ''


def cell(s, n=60):
    s = ' '.join(str(s or '').split()).replace('|', '／')
    return s[:n] + ('…' if len(s) > n else '')


def run(date):
    cfg = util.config()
    lib = util.load(util.p('data', 'games.json'), [])
    lib_keys = {}
    for g in lib:
        for k in [g.get('key')] + g.get('alt_keys', []):
            if k:
                lib_keys[k] = g['name']
    out = [f'# 采集数据初步处理：{date}', '',
           '只做盘点、去重和分类，没有打开游戏页面核实，也没有入库。"过线"指按现行入库线算是否达标，只是标注。', '']

    # ---- 1. 各渠道 ----
    today, earlier = {}, {}
    rows = []
    for ch in CH:
        raw = util.load(util.p('raw', ch, f'{date}.json'))
        prev_keys = set()
        prev_dates = sorted(os.path.basename(p)[:-5] for p in glob.glob(util.p('raw', ch, '*.json')) if os.path.basename(p)[:-5] < date)
        for d in prev_dates:
            prev_keys |= {l['key'] for l in (util.load(util.p('raw', ch, f'{d}.json')) or {}).get('leads', []) if l.get('key')}
        earlier[ch] = prev_keys
        if raw is None:
            rows.append(f'| {CH_NAME[ch]} | 没有文件 | | | |')
            today[ch] = []
            continue
        leads = [l for l in raw.get('leads', [])]
        today[ch] = leads
        new = [l for l in leads if l.get('key') and l['key'] not in prev_keys]
        rows.append(f"| {CH_NAME[ch]} | {len(leads)} | {sum(1 for l in leads if l.get('passes'))} | "
                    f"{len(new) if prev_dates else '（没有更早的数据）'} | {raw.get('error') or '正常'} |")
    out += ['## 1. 各渠道采到多少', '', '| 渠道 | 条数 | 其中过线 | 之前没出现过的 | 状态 |', '|---|---|---|---|---|'] + rows + ['']

    # ---- 2. 跨渠道去重 ----
    by_key = {}
    nolink = 0
    for ch in CH:
        for l in today[ch]:
            if not l.get('key'):
                nolink += 1
                continue
            by_key.setdefault(l['key'], []).append(l)
    multi = {k: v for k, v in by_key.items() if len({l['channel'] for l in v}) > 1}
    total = sum(len(v) for v in today.values())
    out += ['## 2. 跨渠道去重', '',
            f'- 全部线索 {total} 条，其中 {nolink} 条没有外部链接（论坛帖子没贴链接等）。',
            f'- 按链接去重后 {len(by_key)} 条。',
            f'- 同时出现在两个以上渠道的 {len(multi)} 条。',
            f'- 已经在收录库里的 {sum(1 for k in by_key if k in lib_keys)} 条。', '']
    if multi:
        out += ['| 名称 | 链接 | 出现的渠道和数字 |', '|---|---|---|']
        for k, v in multi.items():
            out.append(f"| {cell(v[0].get('title'), 40)} | {v[0]['url']} | {'；'.join(CH_NAME[l['channel']] + ' ' + metric(l)[1] for l in v)} |")
        out.append('')

    # ---- 3. 链接类型 ----
    kinds = {}
    for k, v in by_key.items():
        kd = x_ingest.classify_link(v[0]['url'], cfg)
        v[0]['_kind'] = kd
        kinds.setdefault(kd, []).append(v[0])
    out += ['## 3. 链接类型', '', '按链接落在哪类站点粗分，没有打开页面。文章、视频、商店这几类基本不是可玩的游戏。', '',
            '| 类型 | 条数 | 主要来自 |', '|---|---|---|']
    for kd, ls in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        src = {}
        for l in ls:
            src[l['channel']] = src.get(l['channel'], 0) + 1
        out.append(f"| {KIND.get(kd, kd)} | {len(ls)} | {'、'.join(f'{CH_NAME[c]} {n}' for c, n in sorted(src.items(), key=lambda x: -x[1])[:4])} |")
    out.append('')

    # ---- 4. 过线的线索 ----
    passed = [(k, v) for k, v in by_key.items() if any(l.get('passes') for l in v)]
    out += ['## 4. 按现行入库线过线的线索', '', f'共 {len(passed)} 条。"新"表示之前的采集里没出现过。', '',
            '| 新 | 名称 | 渠道和数字 | 链接类型 | 发布日期 | AI 线索 | 链接 | 备注 |', '|---|---|---|---|---|---|---|---|']
    for ch in CH:
        for k, v in sorted(passed, key=lambda kv: -max(metric(l)[0] for l in kv[1])):
            first = next(l for l in v if l.get('passes'))
            if first['channel'] != ch:
                continue
            is_new = not any(k in earlier[l['channel']] for l in v)
            note = '已在收录库：' + lib_keys[k] if k in lib_keys else ''
            out.append(f"| {'新' if is_new else ''} | {cell(first.get('title'), 44)} | "
                       f"{'；'.join(CH_NAME[l['channel']] + ' ' + metric(l)[1] for l in v)} | {KIND.get(v[0].get('_kind', ''), '')} | "
                       f"{first.get('published') or '未知'} | {cell(first.get('ai_hint'), 36)} | {v[0]['url']} | {note} |")
    out.append('')

    # ---- 5. 没过线但数字靠前的 ----
    out += ['## 5. 没过线、但数字靠前的线索', '', '每个渠道列前 8 条，供定筛选标准时参考。', '',
            '| 渠道 | 名称 | 数字 | 链接类型 | 链接 |', '|---|---|---|---|---|']
    for ch in CH:
        rest = [l for l in today[ch] if not l.get('passes') and l.get('key') and l['key'] not in lib_keys]
        for l in sorted(rest, key=lambda l: -metric(l)[0])[:8]:
            kd = x_ingest.classify_link(l['url'], cfg)
            out.append(f"| {CH_NAME[ch]} | {cell(l.get('title'), 48)} | {metric(l)[1]} | {KIND.get(kd, kd)} | {l['url']} |")
    out.append('')

    # ---- 6. X 帖子 ----
    xr = util.load(util.p('raw', 'x', f'{date}.json'))
    out += ['## 6. X 帖子', '']
    if not xr:
        out += ['今天没有 X 数据（拉取地址还没有配置，手动补充清单里也没有今天的帖子）。', '']
    else:
        out += [f"- 记录 {xr.get('total_records')} 条（其中手动补充 {xr.get('manual_records', 0)} 条），粗筛命中 {xr.get('coarse_hits')} 条。",
                f"- 规则加核验的判断：{xr.get('verdicts')}。", '',
                '| 账号 | 浏览 | 判断 | 游戏或链接 | 正文开头 |', '|---|---|---|---|---|']
        for it in sorted(xr.get('items', []), key=lambda i: -(i.get('views') or 0)):
            g = '；'.join((x.get('name') or '') + ' ' + x.get('url', '') for x in it.get('games') or []) or \
                ('没找到链接，可先以帖子入库' if it.get('linkless_ok') else '')
            out.append(f"| @{it.get('handle')} | {util.fmt_num(it.get('views'))} | {it.get('verdict')} | {cell(g, 70)} | {cell(it.get('text'), 60)} |")
        out.append('')

    os.makedirs(util.p('reports'), exist_ok=True)
    path = util.p('reports', f'初步处理-{date}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    print(f'[prelim] {date}：线索 {total} 条，去重后 {len(by_key)} 条，过线 {len(passed)} 条，跨渠道 {len(multi)} 条 → {path}')
    return path


if __name__ == '__main__':
    run(sys.argv[1] if len(sys.argv) > 1 else util.today())
