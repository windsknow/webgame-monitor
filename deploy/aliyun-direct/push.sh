#!/usr/bin/env bash
# 本机执行：把代码和采集状态传到服务器。用法：bash deploy/aliyun-direct/push.sh [主机，默认 wzry]
# 不传 .git、网页、对方送来的原始帖子和内部体验记录。传完后在服务器上跑一次 install.sh。
set -euo pipefail
cd "$(dirname "$0")/../.."
HOST="${1:-wzry}"
rsync -az --exclude .git --exclude site --exclude __pycache__ --exclude 'inbox/x/*.json' \
  --exclude data/private --exclude data/cache --exclude backtest --exclude assets \
  ./ "$HOST":/opt/webgame-monitor/
echo "已传到 $HOST:/opt/webgame-monitor"
