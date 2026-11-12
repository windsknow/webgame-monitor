#!/usr/bin/env bash
# 本机执行：把服务器上采到的数据拉回来。用法：bash deploy/aliyun-direct/pull.sh [主机，默认 wzry]
# 只拉 raw/、榜单状态和运行记录，不动本机的收录库和线索队列。
set -euo pipefail
cd "$(dirname "$0")/../.."
HOST="${1:-wzry}"
rsync -az "$HOST":/opt/webgame-monitor/raw/ raw/
rsync -az "$HOST":/opt/webgame-monitor/data/state/ data/state/
rsync -az "$HOST":/opt/webgame-monitor/data/runlog.json data/runlog.json
ls raw
