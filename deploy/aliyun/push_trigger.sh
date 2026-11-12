#!/usr/bin/env bash
# 本机执行：把触发脚本和定时器文件传到服务器的 /opt/webgame-monitor/deploy/。
# 用法：bash deploy/aliyun/push_trigger.sh [主机，默认 wzry]
# 传完后在服务器上执行 sudo bash /opt/webgame-monitor/deploy/install_trigger.sh
set -euo pipefail
cd "$(dirname "$0")"
HOST="${1:-wzry}"
ssh "$HOST" 'mkdir -p /opt/webgame-monitor/deploy'
scp -q trigger_github.sh install_trigger.sh uninstall_trigger.sh webgame-trigger.service webgame-trigger.timer \
  "$HOST":/opt/webgame-monitor/deploy/
ssh "$HOST" 'chmod +x /opt/webgame-monitor/deploy/*.sh && ls -l /opt/webgame-monitor/deploy'
