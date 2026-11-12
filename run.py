#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管线入口。

  python3 run.py crawl [日期]     只采集：固定渠道 + X 数据接入，存进 raw/，不合并、不入库。现在线上每天跑的是这个
  python3 run.py daily [日期]     采集之后接着合并入库、出汇总（筛选标准定下来之后再用）
  python3 run.py collect [日期]   只跑固定渠道采集
  python3 run.py x [日期]         只跑 X 数据接入（读 inbox/x/<日期>.json，没有就按配置去拉）
  python3 run.py merge [日期]     只跑合并打分
  python3 run.py site [日期]      只生成页面
日期默认是今天（UTC+8 的日期由调用方决定，可用环境变量 MONITOR_DATE 指定）。
"""
import sys

from monitor import build_site, collectors, merge, util, x_ingest


def crawl(date, log=None):
    """只采集，不筛选：各渠道的线索和 X 帖子的粗筛结果存进 raw/，榜单状态存进 data/state/。"""
    final = log is None
    log = log or {'date': date}
    log['mode'] = 'crawl' if final else 'daily'
    log['collect'] = collectors.run(date)
    # X 数据通常在当天上午送到；顺带补跑前两天里还没处理过的文件
    import datetime, os
    xs = {}
    for back in (2, 1, 0):
        d = (datetime.date.fromisoformat(date) - datetime.timedelta(days=back)).isoformat()
        # 前两天的：对方的文件已经处理过才跳过。只处理过手动补充帖子的（fetch_time 为空）要再试一次，
        # 因为采集时间早于对方文件的生成时间时，当天的文件要到第二天才拉得到
        if back and (util.load(util.p('raw', 'x', f'{d}.json')) or {}).get('fetch_time'):
            continue
        r = x_ingest.run(d)
        if r:
            xs[d] = {k: r[k] for k in ('total_records', 'coarse_hits', 'verdicts', 'in_library')}
    log['x'] = xs
    if final:
        save_log(log)
        bad = [k for k, v in log['collect'].items() if v['error']]
        print(f"[crawl] {date} 完成：{sum(v['count'] for v in log['collect'].values())} 条线索"
              + (f"；出错的渠道：{'、'.join(bad)}" if bad else '；各渠道都取到了数'))
    return log


def save_log(log):
    runs = util.load(util.p('data', 'runlog.json'), [])
    runs = [r for r in runs if r['date'] != log['date']] + [log]
    util.save(util.p('data', 'runlog.json'), runs[-120:])


def daily(date):
    log = crawl(date, {'date': date})
    log['merge'] = merge.run(date)
    log['site'] = build_site.run(date)
    save_log(log)
    return log


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'daily'
    date = sys.argv[2] if len(sys.argv) > 2 else util.today()
    res = {'crawl': crawl, 'daily': daily, 'collect': collectors.run, 'x': x_ingest.run, 'merge': merge.run,
           'site': build_site.run}[cmd](date)
    # 只采集时：所有渠道都出错（多半是网络不通）才算失败，让定时任务能看到
    if cmd == 'crawl' and all(v['error'] for v in res['collect'].values()):
        sys.exit(1)
