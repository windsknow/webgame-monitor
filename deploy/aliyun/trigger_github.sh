#!/usr/bin/env bash
# 从阿里云服务器定时触发 GitHub Actions 里的采集任务。写法和 taptap-spider 的 trigger_github.sh 一致。
#
# 为什么绕这一圈：服务器访问不了 itch.io、t.co 短链和一部分游戏托管站点，采集要在 GitHub 的机器上跑；
# 而 GitHub 自己的 schedule 触发延迟大、不稳定。所以服务器只当闹钟（systemd 定时器），
# 用 workflow_dispatch 触发，通常几秒内就开始。
#
# 用法: trigger_github.sh [日期 YYYY-MM-DD，默认今天]　　加 --dry-run 只打印不发送
# 令牌: /etc/webgame-monitor/github_token（600，只有 root 能读）
# 仓库: 默认 windsknow/webgame-monitor；要改就把 "用户名/仓库名" 写进 /etc/webgame-monitor/repo
set -euo pipefail

DRY=0
if [ "${1:-}" = "--dry-run" ]; then DRY=1; shift; fi

CONF_DIR=/etc/webgame-monitor
TOKEN_FILE="$CONF_DIR/github_token"
REPO="windsknow/webgame-monitor"
[ -r "$CONF_DIR/repo" ] && REPO="$(tr -d ' \n\r' < "$CONF_DIR/repo")"
WORKFLOW="daily.yml"

# 把北京日期显式传给工作流：GitHub 那边晚几分钟起跑，数据也落在正确的日期上
TODAY="${1:-$(TZ=Asia/Shanghai date +%F)}"
BODY="{\"ref\":\"main\",\"inputs\":{\"date\":\"$TODAY\"}}"
URL="https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/dispatches"

echo "$(TZ=Asia/Shanghai date '+%F %T') 触发采集 (repo=$REPO date=$TODAY)"
if [ "$DRY" = "1" ]; then
  echo "只打印不发送：POST $URL"
  echo "$BODY"
  exit 0
fi

[ -r "$TOKEN_FILE" ] || { echo "读不到令牌文件 $TOKEN_FILE" >&2; exit 1; }
TOKEN="$(tr -d ' \n\r' < "$TOKEN_FILE")"

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT
code=$(curl -s -o "$OUT" -w '%{http_code}' \
  -X POST "$URL" \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  --max-time 30 \
  -d "$BODY")

# 成功时 GitHub 返回 204 No Content，响应体是空的
if [ "$code" = "204" ]; then
  echo "已触发，去 https://github.com/$REPO/actions 看运行情况"
  exit 0
fi

echo "触发失败 HTTP $code" >&2
cat "$OUT" >&2
case "$code" in
  401) echo "→ 令牌无效或已过期，重新生成一个" >&2 ;;
  403) echo "→ 令牌权限不够，需要对该仓库的 Actions: Read and write" >&2 ;;
  404) echo "→ 仓库名或工作流文件名不对，也可能是令牌没勾选这个仓库" >&2 ;;
  422) echo "→ 传入的 inputs 不被工作流接受，检查 daily.yml 的 inputs 定义" >&2 ;;
esac
exit 1
