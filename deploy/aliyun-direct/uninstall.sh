#!/usr/bin/env bash
# 服务器上执行：停掉定时采集。数据和代码留在 /opt/webgame-monitor，不删除。
set -euo pipefail
systemctl disable --now webgame-crawl.timer || true
rm -f /etc/systemd/system/webgame-crawl.service /etc/systemd/system/webgame-crawl.timer
systemctl daemon-reload
echo "已停用。数据还在 /opt/webgame-monitor。"
