#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管线入口。

  python3 run.py daily [日期]     每天跑：固定渠道采集 → X 数据接入 → 合并打分 → 生成页面
  python3 run.py collect [日期]   只跑固定渠道采集
  python3 run.py x [日期]         只跑 X 数据接入（读 inbox/x/<日期>.json，没有就按配置去拉）
  python3 run.py merge [日期]     只跑合并打分
  python3 run.py site [日期]      只生成页面
日期默认是今天（UTC+8 的日期由调用方决定，可用环境变量 MONITOR_DATE 指定）。
"""
import sys

from monitor import build_site, collectors, merge, util, x_ingest


def daily(date):
    log = {'date': date}
    log['collect'] = collectors.run(date)
    # X 数据通常在当天上午送到；顺带补跑前两天里还没处理过的文件
    import datetime, os
    xs = {}
    for back in (2, 1, 0):
        d = (datetime.date.fromisoformat(date) - datetime.timedelta(days=back)).isoformat()
        if back and os.path.exists(util.p('raw', 'x', f'{d}.json')):
            continue
        r = x_ingest.run(d)
        if r:
            xs[d] = {k: r[k] for k in ('total_records', 'coarse_hits', 'verdicts', 'in_library')}
    log['x'] = xs
    log['merge'] = merge.run(date)
    log['site'] = build_site.run(date)
    runs = util.load(util.p('data', 'runlog.json'), [])
    runs = [r for r in runs if r['date'] != date] + [log]
    util.save(util.p('data', 'runlog.json'), runs[-120:])
    return log


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'daily'
    date = sys.argv[2] if len(sys.argv) > 2 else util.today()
    {'daily': daily, 'collect': collectors.run, 'x': x_ingest.run, 'merge': merge.run, 'site': build_site.run}[cmd](date)
