#!/bin/bash
# 连通性测试：换部署位置之前，先在候选机器上跑一遍，看各渠道能不能访问。
# 用法：ssh <主机> 'bash -s' < scripts/nettest.sh　或在机器上直接 bash scripts/nettest.sh
# 只读：每个地址发一次 GET，打印 curl 退出码、状态码、耗时、大小。不写任何文件。
# 看法：rc=0 且状态码 200 才算通；rc=28 是超时，rc=7 和 rc=35 是连不上。
UA='webgame-monitor/1.0 (+curl)'
t() {
  name="$1"; url="$2"
  out=$(curl -s -L -m 20 -A "$UA" -o /dev/null -w '%{http_code} %{time_total}s %{size_download}B' "$url" 2>/dev/null)
  rc=$?
  printf '%-28s rc=%-3s %s\n' "$name" "$rc" "$out"
}
echo "== host: $(hostname)  $(date '+%F %T %Z')"
echo "-- 采集要用的接口"
t hn_algolia        'https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=1'
t threejs_forum     'https://discourse.threejs.org/c/showcase/7/l/top.json?period=weekly'
t github_api        'https://api.github.com/search/repositories?q=browser+game&per_page=1'
t github_raw        'https://raw.githubusercontent.com/lappemic/awesome-ai-built-games/HEAD/README.md'
t itch_rss          'https://itch.io/games/new-and-popular/platform-web.xml'
t spawn_api         'https://www.spawn.co/api/feed?sort=top'
t verse8_api        'https://v8-meme-api.verse8.io/v1/verse?page=1&limit=1&featured=false&sortBy=trending&excludeHidden=false'
t astrocade         'https://www.astrocade.com/category/trending'
t rosebud           'https://rosebud.ai/'
t wavedash          'https://wavedash.com/'
echo "-- X 帖子要用的"
t fxtwitter         'https://api.fxtwitter.com/threejs/status/2120448192324276227'
t t_co_shortlink    'https://t.co/rBfMyRkZzm'
t x_com             'https://x.com/threejs/status/2120448192324276227'
echo "-- 以后筛选时要打开的游戏页面"
t page_github_io    'https://loktar00.github.io/lanternfall/'
t page_claude_ai    'https://claude.ai/artifact/BGY1SztrAYcJQHSsnHHGmp'
t page_vercel_app   'https://fantos-megamart.vercel.app/'
t page_own_domain   'https://www.ghanastreetgames.com/'
t page_itch_game    'https://itch.io/games/platform-web'
t page_pages_dev    'https://pages.dev/'
t page_netlify      'https://www.netlify.com/'
echo "-- 把数据推回 GitHub 要用的"
t github_com        'https://github.com/lappemic/awesome-ai-built-games'
