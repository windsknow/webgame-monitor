#!/usr/bin/env bash
# 服务器上执行：停掉定时触发。令牌文件和 /opt/webgame-monitor 不删除。
set -euo pipefail
systemctl disable --now webgame-trigger.timer || true
rm -f /etc/systemd/system/webgame-trigger.service /etc/systemd/system/webgame-trigger.timer
systemctl daemon-reload
echo "已停用定时触发。"
