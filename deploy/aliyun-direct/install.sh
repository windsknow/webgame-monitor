#!/usr/bin/env bash
# 服务器上执行：安装或更新定时采集。用法：sudo bash /opt/webgame-monitor/deploy/aliyun-direct/install.sh
# 代码要先用本机的 deploy/aliyun-direct/push.sh 传上来。只用系统自带的 python3 和 curl，不建虚拟环境。
set -euo pipefail

APP_DIR=/opt/webgame-monitor
RUN_USER=webgamemon

echo ">> 1. 运行用户与目录"
id -u "$RUN_USER" >/dev/null 2>&1 || useradd -r -m -s /sbin/nologin "$RUN_USER"
mkdir -p "$APP_DIR"/{raw,data/state,data/cache,inbox/x}
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

echo ">> 2. systemd 定时器"
cp "$APP_DIR"/deploy/aliyun-direct/webgame-crawl.service "$APP_DIR"/deploy/aliyun-direct/webgame-crawl.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now webgame-crawl.timer
systemctl list-timers 'webgame-*' --no-pager
