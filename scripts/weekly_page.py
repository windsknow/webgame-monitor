# -*- coding: utf-8 -*-
"""周报网页：一周新入库的游戏，卡片样式和信息流页面相同（build_site.card）。

python3 scripts/weekly_page.py <起始入库日期> <结束入库日期> <输出目录> [编辑稿.json]

编辑稿（可选）写周报标题、导语、推荐和现象，格式：
{"eyebrow": "...", "title": "...", "lede": "...",
 "tiers": [{"title": "第一档：…", "sub": "…", "items": [{"name": "游戏名", "what": "一句话", "why": "推荐理由"}]}],
 "trends": [{"h": "标题", "p": "正文"}], "notes": ["口径说明", "..."]}
没有编辑稿时，开头的推荐按重点卡片自动列出（X 浏览、热度从高到低）。
输出目录里是 index.html、assets/style.css 和用到的截图、图标，可以直接发布。
"""
import html
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitor import util                                                      # noqa: E402
from monitor.build_site import card, heat                              # noqa: E402
from monitor.feed import CH_SHORT, channels_of                                # noqa: E402

e = html.escape

CSS = '''
.wk-sec{padding:40px 0 8px}
.minis{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px}
.mini{overflow:hidden;display:flex;flex-direction:column}
.mini .mth{display:block;aspect-ratio:16/9;background:var(--surface-2);overflow:hidden}
.mini .mth img{width:100%;height:100%;object-fit:cover;display:block}
.mb{padding:12px 14px 14px;display:flex;flex-direction:column;gap:6px;flex:1;min-width:0}
.mb h3{margin:0;font-size:16.5px;line-height:1.35;overflow-wrap:anywhere}
.mb h3 small{font-size:12.5px;font-weight:400;color:var(--text-3);margin-left:6px}
.mb p{margin:0;font-size:14px}
.mb .why{color:var(--text-2);font-size:13.5px}
.mb .mnum{font-size:13px;color:var(--hot);font-variant-numeric:tabular-nums}
.ml{display:flex;gap:14px;flex-wrap:wrap;margin-top:auto!important;padding-top:4px;font-size:13.5px;font-weight:600}
.trends{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.trend{padding:16px 18px}.trend h3{margin:0 0 6px;font-size:16px}.trend p{margin:0;font-size:14px;color:var(--text-2)}
.chips{display:flex;flex-wrap:wrap;gap:6px;position:sticky;top:0;z-index:5;background:var(--bg);padding:10px 0;margin-bottom:6px}
.chip{border:1px solid var(--border);background:var(--surface);color:var(--text);font:inherit;font-size:13px;padding:4px 12px;border-radius:999px;cursor:pointer}
.chip i{font-style:normal;color:var(--text-3);font-size:12px;margin-left:3px}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}.chip.on i{color:rgba(255,255,255,.8)}
.grp{margin-top:22px}.grp>h3{font-size:18px;margin:0 0 12px}.grp>h3 span{font-size:13px;font-weight:400;color:var(--text-3);margin-left:6px}
.g{scroll-margin-top:120px}
.bars{display:grid;gap:7px;max-width:760px}
.bar{display:grid;grid-template-columns:8.5em minmax(0,1fr) 2.5em;align-items:center;gap:10px;font-size:13.5px}
.bar i{display:block;height:10px;border-radius:5px;background:var(--accent);opacity:.85}
.bar em{font-style:normal;text-align:right;color:var(--text-2);font-variant-numeric:tabular-nums}
.notes{margin:0;padding-left:1.2em;display:grid;gap:5px;font-size:14px;color:var(--text-2);max-width:900px}
@media (max-width:560px){.wrap{padding:0 16px}h1{font-size:26px}.chips{position:static}.g{scroll-margin-top:16px}}
'''


def x_views(g):
    for s in g.get('stats') or []:
        if s.get('label', '').startswith('X '):
            m = re.match(r'([\d.]+)\s*(万)?', s['value'].replace(',', ''))
            if m:
                return float(m.group(1)) * (10000 if m.group(2) else 1)
    return 0


def shot(g):
    if g.get('shot'):
        return f'<img src="assets/games/{e(g["shot"])}" alt="{e(g["name"])} 的画面" loading="lazy">'
    return f'<span class="noshot"><span>{e(g["name"])}</span></span>'


def mini(g, what, why=''):
    num = '；'.join(f"{s['label']} {s['value']}" for s in (g.get('stats') or [])[:2] if not s.get('doubt'))
    go = '看原帖 ↗' if 'x.com/' in g['url'] else '打开 ↗'
    return (f'<article class="mini card"><a class="mth" href="#{e(g["id"])}">{shot(g)}</a><div class="mb">'
            f'<h3>{e(g["name"])}<small>{e(g.get("kind") or "")}</small></h3><p>{e(what)}</p>'
            + (f'<p class="why">{e(why)}</p>' if why else '') + (f'<p class="mnum">{e(num)}</p>' if num else '')
            + f'<p class="ml"><a href="#{e(g["id"])}">看详情 ↓</a><a href="{e(g["url"])}" target="_blank" rel="noopener">{go}</a></p></div></article>')


def run(d0, d1, out, draft=None):
    games = [g for g in util.load(util.p('data', 'games.json'), []) if d0 <= g.get('added', '') <= d1]
    order = [m['title'] for m in util.load(util.p('data', 'static.json'), {})['groups']]
    by_name = {g['name']: g for g in games}
    os.makedirs(os.path.join(out, 'assets', 'games'), exist_ok=True)
    shutil.copyfile(util.p('templates', 'style.css'), os.path.join(out, 'assets', 'style.css'))
    for g in games:
        for k in ('shot', 'icon'):
            if g.get(k) and os.path.exists(util.p('assets', 'games', g[k])):
                shutil.copyfile(util.p('assets', 'games', g[k]), os.path.join(out, 'assets', 'games', g[k]))

    # 开头的推荐：有编辑稿用编辑稿，没有就列重点
    tiers = []
    for t in (draft or {}).get('tiers') or []:
        items = ''.join(mini(by_name[i['name']], i.get('what', ''), i.get('why', '')) for i in t['items'] if i['name'] in by_name)
        tiers.append(f'<section class="wk-sec"><h2>{e(t["title"])}</h2><p class="sub">{e(t.get("sub", ""))}</p><div class="minis">{items}</div></section>')
    if not tiers:
        hot = sorted([g for g in games if g.get('hot')], key=lambda g: (-x_views(g), -heat(g)))
        if hot:
            items = ''.join(mini(g, g.get('play', '')[:60] + ('…' if len(g.get('play', '')) > 60 else ''), g.get('highlight', '')) for g in hot)
            tiers.append(f'<section class="wk-sec"><h2>本周重点</h2><p class="sub">传播或游玩数据达到重点线的 {len(hot)} 款，X 浏览高的在前。点"看详情"跳到下方完整卡片。</p><div class="minis">{items}</div></section>')
    trends = (draft or {}).get('trends') or []
    trends_html = ('<section class="wk-sec"><h2>几个现象</h2><div class="trends">' + ''.join(
        f'<article class="trend card"><h3>{e(t["h"])}</h3><p>{e(t["p"])}</p></article>' for t in trends) + '</div></section>') if trends else ''

    # 全部卡片：按方向分组，组内重点在前，再按 X 浏览、热度
    chips = [f'<button class="chip on" data-g="">全部<i>{len(games)}</i></button>']
    secs = []
    groups = order + sorted({g.get('group') or '待归类' for g in games} - set(order))
    for i, grp in enumerate(groups):
        gs = sorted([g for g in games if (g.get('group') or '待归类') == grp], key=lambda g: (not g.get('hot'), -x_views(g), -heat(g)))
        if not gs:
            continue
        chips.append(f'<button class="chip" data-g="g{i}">{e(grp)}<i>{len(gs)}</i></button>')
        secs.append(f'<div class="grp" data-g="g{i}"><h3>{e(grp)}<span>{len(gs)} 款</span></h3><div class="cards">{"".join(card(g) for g in gs)}</div></div>')

    chs = {}
    for g in games:
        for c in channels_of(g):
            chs[c] = chs.get(c, 0) + 1
    top = max(chs.values() or [1])
    bars = ''.join(f'<div class="bar"><span>{e(CH_SHORT.get(k, k))}</span><i style="width:{v / top * 100:.1f}%"></i><em>{v}</em></div>'
                   for k, v in sorted(chs.items(), key=lambda kv: -kv[1]))
    n_hot = sum(1 for g in games if g.get('hot'))
    n_ai = sum(1 for g in games if g.get('ai_tag') == 'ai')
    n_pub = sum(1 for g in games if (re.match(r'\d{4}-\d{2}-\d{2}', g.get('date_text') or '') and g['date_text'][:10] >= d0))
    d = draft or {}
    title = d.get('title') or f'{d0[5:].replace("-", " 月 ", 1)} 日至 {d1[5:].replace("-", " 月 ", 1)} 日新增 {len(games)} 款网页游戏'
    notes = d.get('notes') or []
    notes_html = ''.join(f'<li>{e(n)}</li>' for n in notes + [
        '卡片的数字 X 浏览排第一；每张卡片底部写了数据来源、读取日期和技术栈的依据。',
        '技术栈优先按站点代码特征识别（页面和它引用的脚本里的引擎特征），识别不出的用作者自述、合集站标签或平台说明。亮点是编辑判断。',
        '带"自动入库 · 待核验"标记的没有人工试玩，介绍取自游戏页面、开发者说明和来源帖子。'])
    page = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(d.get('doc_title') or '网页游戏周报')}</title>
<link rel="stylesheet" href="assets/style.css"><style>{CSS}</style></head>
<body>
<header class="top"><div class="wrap">
<p class="eyebrow">{e(d.get('eyebrow') or f'AI 网页游戏新品周报 · 入库日期 {d0} 至 {d1}')}</p>
<h1>{e(title)}</h1>
<p class="lede">{e(d.get('lede') or '')}</p>
<div class="kpis"><div class="kpi card"><div class="v">{len(games)}</div><div class="l">新入库</div></div>
<div class="kpi card"><div class="v">{n_pub}</div><div class="l">{e(d0[5:])} 之后首次公开</div></div>
<div class="kpi card"><div class="v">{n_hot}</div><div class="l">过了重点线</div></div>
<div class="kpi card"><div class="v">{n_ai}</div><div class="l">有 AI 参与证据</div></div></div>
</div></header>
<main class="wrap">
{''.join(tiers)}
{trends_html}
<section class="wk-sec" id="all"><h2>全部 {len(games)} 款</h2><p class="sub">按方向分组，组内重点在前，再按 X 浏览量和其他数字从高到低。</p>
<div class="chips" role="group" aria-label="按方向筛选">{''.join(chips)}</div>{''.join(secs)}</section>
<section class="wk-sec" id="sources"><h2>来源统计</h2><p class="sub">每款游戏是从哪个渠道发现的。一款可能同时出现在几个渠道，所以合计会超过总数；不含溯源时找到的作者原帖和源码仓库。</p><div class="bars">{bars}</div></section>
<section class="wk-sec" id="notes"><h2>口径与限制</h2><ul class="notes">{notes_html}</ul></section>
<p style="height:40px"></p>
</main>
<script>
(function(){{var cs=[].slice.call(document.querySelectorAll('.chip')),gs=[].slice.call(document.querySelectorAll('.grp'));
cs.forEach(function(c){{c.addEventListener('click',function(){{cs.forEach(function(x){{x.classList.toggle('on',x===c)}});
var g=c.getAttribute('data-g');gs.forEach(function(s){{s.hidden=!!g&&s.getAttribute('data-g')!==g}})}})}})}})();
</script>
</body></html>
'''
    with open(os.path.join(out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(page)
    print(f'[weekly] {d0} 至 {d1}：{len(games)} 款，重点 {n_hot} → {out}/index.html ({len(page.encode()) // 1024} KB)')


if __name__ == '__main__':
    a = sys.argv[1:]
    run(a[0], a[1], a[2], json.load(open(a[3], encoding='utf-8')) if len(a) > 3 else None)
