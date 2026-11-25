# -*- coding: utf-8 -*-
"""把每周的玩法新颖（N）、技术前沿（T）打分并进卡片：data/games.json 每张卡片的 novelty 字段。

python3 scripts/apply_novelty.py data/novelty/<日期>.json    并入打分
python3 scripts/apply_novelty.py --queue                     列出本周要打分的卡片

打分文件是数组，每张卡片一条（口径见 docs/重点评判规则.md）：
{"id": "卡片 id", "N": 0|1|2|null, "N_why": "一句话依据（来源类型）",
 "T": 0|1|2|null, "T_why": "…",
 "pending": ["N"]        可选：这一项的 2 分依据不足（只来自合集站简介、清单描述或代码特征），记"待核"，不算重点
 "dup_of": "卡片 id"     可选：同一原作的移植只取一款当重点，其余写被选中那款的 id，卡片上标"同类移植"
 "dup_note": "…"         可选：同类移植的说明
 "recheck": "…"          可选：下次复核要确认什么（比如"试玩确认机制少见后可升到 2 分"）
 "xcheck": "…"           可选：人工复核结论，写进卡片的"交叉验证"}
null 表示"待定"（依据不足，不给分）。文件名里的日期记为 checked。
没出现在文件里的卡片不动。并完之后跑 python3 run.py merge 重算重点。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitor import util                                                      # noqa: E402

KEYS = ('N', 'N_why', 'T', 'T_why', 'pending', 'dup_of', 'dup_note', 'recheck')


def run(path):
    rows = util.load(path, [])
    checked = (re.search(r'\d{4}-\d\d-\d\d', os.path.basename(path)) or [util.today()])[0]
    lib = util.load(util.p('data', 'games.json'), [])
    by_id = {g['id']: g for g in lib}
    miss, n = [], 0
    for r in rows:
        g = by_id.get(r['id'])
        if not g:
            miss.append(r['id'])
            continue
        for k in ('N', 'T'):
            if r.get(k) not in (0, 1, 2, None):
                raise ValueError(f"{r['id']} 的 {k} 只能是 0、1、2 或 null，现在是 {r.get(k)!r}")
        g['novelty'] = {k: r[k] for k in KEYS if r.get(k) not in (None, '', [])} | {'N': r.get('N'), 'T': r.get('T'),
                                                                                   'checked': checked}
        if r.get('xcheck'):
            g['xcheck'] = r['xcheck']
        n += 1
    util.save(util.p('data', 'games.json'), lib)
    print(f'[novelty] {path}：并入 {n} 张卡片' + (f'；库里找不到的 id：{miss}' if miss else ''))


def queue():
    """本周要打分的：还没打过分的卡片（按每天预筛的优先 / 普通 / 降级排，同档按 X 浏览和其他数字），
    以及上次记了待核或写了 recheck 的。"""
    from monitor.build_site import order_key
    lib = util.load(util.p('data', 'games.json'), [])
    rank = {'优先': 0, '普通': 1, '降级': 2}
    todo = sorted([g for g in lib if not g.get('novelty')],
                  key=lambda g: (rank.get((g.get('prescreen') or {}).get('level'), 1), [-x for x in order_key(g)[3:]]))
    again = [g for g in lib if (g.get('novelty') or {}).get('pending') or (g.get('novelty') or {}).get('recheck')]
    print(f'还没打分 {len(todo)} 张：')
    for g in todo:
        ps = g.get('prescreen') or {}
        print(f"  [{ps.get('level', '普通')}] {g['id']}  {g['name']}  ——{ps.get('why', '')}")
    print(f'待核或要复查 {len(again)} 张：')
    for g in again:
        nv = g['novelty']
        print(f"  {g['id']}  {g['name']}  ——" + ('待核：' + '、'.join(nv['pending']) if nv.get('pending') else nv.get('recheck', '')))


if __name__ == '__main__':
    queue() if sys.argv[1] == '--queue' else run(sys.argv[1])
