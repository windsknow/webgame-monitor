#!/usr/bin/env bash
# 在阿里云服务器上装定时触发：每天 7:00 调用 GitHub 接口，触发 Actions 里的采集。
# 前提：GitHub 仓库已经建好并推送过代码；令牌已经写进 /etc/webgame-monitor/github_token
# 用法: sudo bash /opt/webgame-monitor/deploy/install_trigger.sh
set -euo pipefail

APP_DIR=/opt/webgame-monitor
CONF_DIR=/etc/webgame-monitor
TOKEN_FILE="$CONF_DIR/github_token"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "缺少令牌文件 $TOKEN_FILE" >&2
  echo "请先执行（把 <令牌> 换成你在 GitHub 生成的那串）：" >&2
  echo "  sudo mkdir -p $CONF_DIR" >&2
  echo "  sudo sh -c 'echo <令牌> > $TOKEN_FILE'" >&2
  echo "  sudo chmod 600 $TOKEN_FILE" >&2
  exit 1
fi
chmod 600 "$TOKEN_FILE"

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$APP_DIR/deploy"
# 文件可能已经在 $APP_DIR/deploy 里了，此时源和目标是同一个文件，不用复制
if [ "$SRC" != "$APP_DIR/deploy" ]; then
  cp -f "$SRC"/trigger_github.sh "$APP_DIR/deploy/"
fi
chmod +x "$APP_DIR/deploy/trigger_github.sh"

echo ">> 先试触发一次，确认令牌和仓库名可用"
"$APP_DIR/deploy/trigger_github.sh"

echo ">> 安装定时器"
cp -f "$SRC"/webgame-trigger.service "$SRC"/webgame-trigger.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now webgame-trigger.timer
systemctl list-timers 'webgame-*' --no-pager
