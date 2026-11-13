# -*- coding: utf-8 -*-
"""信息流网页：site/index.html。

结构：顶栏（标题、检索、深浅色）→ 重点游戏头图轮播 → 标签筛选和排序 → 按入库日期倒序的游戏卡片流 → 方法和口径说明。
游戏卡片沿用上一份报告的样式（build_site.card 和 templates/style.css），信息流和轮播的样式在 templates/feed.css。
页面只读 data/ 下的文件，不联网；检索、筛选和排序都在浏览器里做，不需要服务器。
"""
import datetime
import html
import json
import os
import re
import shutil

from . import util
from .build_site import AI_TAG, CHANNEL, LINK, PAL, card, date_key, heat, method_html

e = html.escape
WEEK = '一二三四五六日'
CH_SHORT = {'x': 'X', 'hn': 'Hacker News', 'threejs': 'three.js 论坛', 'github': 'GitHub', 'itch': 'itch.io',
            'awesome': '精选清单', 'spawn': 'Spawn', 'verse8': 'Verse8', 'astrocade': 'Astrocade', 'rosebud': 'Rosebud',
            'wavedash': 'Wavedash', 'fable52': 'Fable52.games', '基线报告': '首批整理'}
STATUS_TAGS = [('hot', '重点'), ('new', '近 7 天入库'), ('verified', '已核验'), ('auto', '待核验'), ('nolink', '试玩链接未找到')]


def channels_of(g):
    chs = [s['channel'] for s in g.get('sources') or []]
    fs = (g.get('first_seen') or {}).get('channel')
    if fs:
        chs.append(fs)
    return list(dict.fromkeys(c for c in chs if c in CH_SHORT))


def day_label(d):
    try:
        x = datetime.date.fromisoformat(d)
        return f'{x.month} 月 {x.day} 日 · 周{WEEK[x.weekday()]}'
    except ValueError:
        return d


def hero(g):
    col = PAL[sum(map(ord, g['id'])) % len(PAL)]
    img = (f'<img src="assets/games/{e(g["shot"])}" alt="{e(g["name"])} 画面" loading="lazy">' if g.get('shot')
           else f'<div class="noshot" style="--c:{col}"><span>{e(g["name"])}</span></div>')
    cls, lab = AI_TAG.get(g.get('ai_tag') or 'unlabeled', AI_TAG['unlabeled'])
    stats = ''.join(f'<div><b>{e(s["value"])}</b><span>{e(s["label"])}</span></div>'
                    for s in (g.get('stats') or [])[:3] if not s.get('doubt'))
    text = g.get('play') or html.unescape(g.get('quote') or '')
    sig = '；'.join(s['说明'] for s in g.get('signals') or [] if s['类别'] != '技术')
    go = LINK.get(g.get('link_status'), ('', '试玩 ↗'))[1]
    return f'''<article class="slide" id="hero-{e(g['id'])}">
<a class="slide-img" href="{e(g['url'])}" target="_blank" rel="noopener">{img}</a>
<div class="slide-body">
<div class="slide-tags"><span class="hot-pill">★ 重点</span><span class="tag {cls}">{lab}</span>{f'<span class="tag tag-grp">{e(g["group"])}</span>' if g.get('group') and g['group'] != '待归类' else ''}</div>
<h3>{e(g['name'])}</h3>
<p class="slide-kind">{e(g.get('kind') or '')}{' · ' if g.get('kind') else ''}{e(g.get('date_text') or '')}</p>
<p class="slide-text">{e(text)}</p>
{f'<p class="slide-why">重点依据：{e(sig)}</p>' if sig else ''}
<div class="slide-stats">{stats}</div>
<div class="slide-act"><a class="btn" href="{e(g['url'])}" target="_blank" rel="noopener">{go}</a><a class="btn btn-2" href="#{e(g['id'])}">看卡片</a></div>
</div>
</article>'''


def run(date=None):
    cfg = util.config()
    date = date or util.today()
    games = util.load(util.p('data', 'games.json'), [])
    st = util.load(util.p('data', 'static.json'), {})
    out = util.p('site')
    os.makedirs(os.path.join(out, 'assets'), exist_ok=True)
    for css in ('style.css', 'feed.css'):
        shutil.copyfile(util.p('templates', css), os.path.join(out, 'assets', css))
    if os.path.isdir(os.path.join(out, 'assets', 'games')):
        shutil.rmtree(os.path.join(out, 'assets', 'games'))
    shutil.copytree(util.p('assets', 'games'), os.path.join(out, 'assets', 'games'))
    for n in ('favicon.png', 'touch.png'):
        if os.path.exists(util.p('assets', n)):
            shutil.copyfile(util.p('assets', n), os.path.join(out, 'assets', n))

    week_ago = (datetime.date.fromisoformat(date) - datetime.timedelta(days=7)).isoformat()
    groups_order = [m['title'] for m in st['groups']]
    # 重点轮播：最近升为重点的在前，同一天按热度
    hot = sorted([g for g in games if g.get('hot')],
                 key=lambda g: (g.get('hot_since') or g.get('added') or '', heat(g)), reverse=True)[:12]

    # 信息流：按入库日期倒序；同一天里重点在前，再按热度。首批整理的 68 款按首次公开日期排
    days = {}
    for g in games:
        days.setdefault(g.get('added') or '', []).append(g)
    feed_html, all_ch, all_grp = [], {}, {}
    for d in sorted(days, reverse=True):
        gs = days[d]
        base = all(x.get('issue') == '基线' for x in gs)
        gs.sort(key=(lambda g: date_key(g)) if base else (lambda g: (bool(g.get('hot')), heat(g))), reverse=True)
        cards = []
        for g in gs:
            chs = channels_of(g)
            for c in chs:
                all_ch[c] = all_ch.get(c, 0) + 1
            grp = g.get('group') or '待归类'
            all_grp[grp] = all_grp.get(grp, 0) + 1
            st_tags = []
            if g.get('hot'):
                st_tags.append('hot')
            if (g.get('added') or '') > week_ago and g.get('issue') != '基线':
                st_tags.append('new')
            st_tags.append('auto' if g.get('status') == 'auto' else 'verified')
            if g.get('link_status') == 'none':
                st_tags.append('nolink')
            text = ' '.join(str(g.get(k) or '') for k in ('name', 'kind', 'play', 'quote', 'tech', 'group', 'url', 'ai_evidence'))
            text += ' ' + ' '.join(g.get('x_handles') or []) + ' ' + ' '.join(CH_SHORT[c] for c in chs)
            attrs = {'data-grp': grp, 'data-ch': ' '.join(chs), 'data-st': ' '.join(st_tags),
                     'data-q': re.sub(r'\s+', ' ', html.unescape(text)).lower(), 'data-added': g.get('added') or ''}
            c = card(g, 'new' in st_tags)
            c = c.replace('<article class="g card', '<article ' + ' '.join(f'{k}="{e(v)}"' for k, v in attrs.items())
                          + ' class="g card', 1)
            cards.append(c)
        n_hot = sum(1 for g in gs if g.get('hot'))
        title = (f'{day_label(d)} · 首批整理' if base else day_label(d))
        sub = (f'2026-11-10 整理的首批 {len(gs)} 款，按首次公开日期排列' if base
               else f'新入库 {len(gs)} 款' + (f'，其中重点 {n_hot} 款' if n_hot else ''))
        feed_html.append(f'<section class="day" data-day="{e(d)}"><div class="day-h"><h2>{e(title)}</h2><span>{e(sub)}</span></div>'
                         f'<div class="cards">{"".join(cards)}</div></section>')

    def chips(name, items):
        return ''.join(f'<button type="button" class="chip" data-k="{name}" data-v="{e(v)}">{e(lab)}<i>{n}</i></button>'
                       for v, lab, n in items if n)
    grp_items = [(t, t, all_grp.get(t, 0)) for t in groups_order] + \
                [(t, t, n) for t, n in all_grp.items() if t not in groups_order]
    ai_items = [(k, lab, sum(1 for g in games if (g.get('ai_tag') or 'unlabeled') == k)) for k, (_, lab) in AI_TAG.items()]
    ch_items = [(k, CH_SHORT[k], all_ch.get(k, 0)) for k in CH_SHORT]
    filt = f'''<div class="filters" id="filters">
<div class="frow"><span class="fl">状态</span><div class="chips">{''.join(f'<button type="button" class="chip" data-k="st" data-v="{k}">{lab}<i></i></button>' for k, lab in STATUS_TAGS)}</div></div>
<div class="frow"><span class="fl">方向</span><div class="chips">{chips('grp', grp_items)}</div></div>
<div class="frow"><span class="fl">AI 标签</span><div class="chips">{chips('ai', ai_items)}</div></div>
<div class="frow"><span class="fl">发现渠道</span><div class="chips">{chips('ch', ch_items)}</div></div>
</div>'''
    n_hot = sum(1 for g in games if g.get('hot'))
    n_week = sum(1 for g in games if (g.get('added') or '') > week_ago and g.get('issue') != '基线')
    n_ai = sum(1 for g in games if g.get('ai_tag') == 'ai')
    title = cfg['site']['title']
    hero_html = ''.join(hero(g) for g in hot)
    dots = ''.join(f'<button type="button" aria-label="第 {i + 1} 张" data-i="{i}"></button>' for i in range(len(hot)))
    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{e(title)}</title>
<link rel="icon" type="image/png" href="assets/favicon.png">
<link rel="stylesheet" href="assets/style.css">
<link rel="stylesheet" href="assets/feed.css">
</head>
<body class="feed-page">
<header class="bar"><div class="bar-in">
<a class="brand" href="#top">{e(title)}</a>
<label class="search"><svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M13 13l4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><input id="q" type="search" placeholder="搜索游戏名、玩法、技术、作者…" autocomplete="off"></label>
<button id="themeBtn" type="button">深色</button>
</div></header>

<div class="intro" id="top"><div class="wrap2">
<p class="eyebrow">数据截至 {date} · 每天更新</p>
<h1>{e(title)}</h1>
<p class="lede">新出现的、能在浏览器里直接玩的 AI 游戏。每天从 X、Hacker News、three.js 论坛、GitHub、itch.io、各 AI 创作平台和游戏合集站读取线索，判断为网页游戏的入库，传播或游玩数据达到重点线的放在最前面。</p>
<div class="nums"><div><b>{len(games)}</b><span>累计收录</span></div><div><b>{n_week}</b><span>近 7 天入库</span></div><div><b>{n_hot}</b><span>重点</span></div><div><b>{n_ai}</b><span>有 AI 参与证据</span></div></div>
</div></div>

<section class="hero wrap2" aria-label="重点游戏">
<div class="hero-h"><h2>重点游戏</h2><span>传播或游玩数据达到重点线的游戏，最近升为重点的在前</span></div>
<div class="carousel" id="car">
<div class="track" id="track">{hero_html}</div>
<button type="button" class="nav prev" aria-label="上一张">‹</button><button type="button" class="nav next" aria-label="下一张">›</button>
</div>
<div class="dots" id="dots">{dots}</div>
</section>

<main class="wrap2">
<div class="tools" id="tools">
<div class="tools-top"><span class="count" id="count"></span>
<span class="seg" id="sortSeg"><button type="button" data-sort="date" class="on">按入库日期</button><button type="button" data-sort="heat">按热度</button><button type="button" data-sort="pub">按首次公开</button></span>
<button type="button" class="clear" id="clear" hidden>清除筛选</button></div>
{filt}
</div>
<div id="feed">{''.join(feed_html)}</div>
<div class="flat cards" id="flat" hidden></div>
<p class="none" id="none" hidden>没有符合条件的游戏。</p>

<details class="about card"><summary>这些游戏是怎么筛出来的</summary>{method_html(cfg)}</details>
<footer class="end">
<b>口径说明</b>
<ul>
<li>卡片上的数字写明了来源和读取日期。网页游戏热度变化很快，数字会迅速过时；各渠道口径不同，不能直接横比。</li>
<li>带"自动入库 · 待核验"标记的没有经过试玩，介绍取自游戏页面、开发者说明和来源帖子。</li>
<li>Fable52.games 是游戏合集站，属于二手渠道：试玩链接和日期优先取作者帖子、源码仓库等原始出处，找不到时才用合集站给的链接。</li>
<li>同人作品和移植作品与原版权方无关，可能随时下线。画面与图标版权归各自作者所有。</li>
</ul>
</footer>
</main>
<button type="button" id="toTop" aria-label="回到顶部">↑</button>
<script>
(function(){{var b=document.getElementById('themeBtn'),r=document.documentElement;
function cur(){{return r.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light')}}
function lab(){{b.textContent=cur()==='dark'?'浅色':'深色'}}
b.addEventListener('click',function(){{r.setAttribute('data-theme',cur()==='dark'?'light':'dark');lab();try{{localStorage.setItem('theme',r.getAttribute('data-theme'))}}catch(e){{}}}});
try{{var t=localStorage.getItem('theme');if(t)r.setAttribute('data-theme',t)}}catch(e){{}}lab()}})();

(function(){{
var car=document.getElementById('car'),track=document.getElementById('track'),dots=[].slice.call(document.querySelectorAll('#dots button'));
var slides=[].slice.call(track.children);if(!slides.length){{document.querySelector('.hero').hidden=true;return}}
var i=0,timer=null,reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
function go(n,smooth){{i=(n+slides.length)%slides.length;track.scrollTo({{left:slides[i].offsetLeft-track.offsetLeft,behavior:smooth===false||reduce?'auto':'smooth'}});mark()}}
function mark(){{dots.forEach(function(d,k){{d.classList.toggle('on',k===i)}})}}
function play(){{stop();if(!reduce)timer=setInterval(function(){{go(i+1)}},6000)}}
function stop(){{if(timer)clearInterval(timer);timer=null}}
car.querySelector('.prev').addEventListener('click',function(){{go(i-1);play()}});
car.querySelector('.next').addEventListener('click',function(){{go(i+1);play()}});
dots.forEach(function(d,k){{d.addEventListener('click',function(){{go(k);play()}})}});
car.addEventListener('mouseenter',stop);car.addEventListener('mouseleave',play);
car.addEventListener('focusin',stop);
var st;track.addEventListener('scroll',function(){{clearTimeout(st);st=setTimeout(function(){{
var w=track.clientWidth,n=Math.round(track.scrollLeft/w);if(n!==i){{i=n;mark()}}}},80)}},{{passive:true}});
document.addEventListener('keydown',function(ev){{if(ev.target.tagName==='INPUT')return;var r=car.getBoundingClientRect();if(r.bottom<0||r.top>innerHeight)return;
if(ev.key==='ArrowLeft'){{go(i-1);play()}}if(ev.key==='ArrowRight'){{go(i+1);play()}}}});
mark();play();
}})();

(function(){{
var feed=document.getElementById('feed'),flat=document.getElementById('flat'),none=document.getElementById('none');
var cards=[].slice.call(feed.querySelectorAll('article.g'));
cards.forEach(function(c){{c._home=c.parentNode}});
var q=document.getElementById('q'),count=document.getElementById('count'),clear=document.getElementById('clear');
var sel={{st:{{}},grp:{{}},ai:{{}},ch:{{}}}},sort='date';
var chipEls=[].slice.call(document.querySelectorAll('#filters .chip'));
function has(c,k,v){{if(k==='grp')return c.dataset.grp===v;if(k==='ai')return c.dataset.ai===v;return (' '+c.dataset[k]+' ').indexOf(' '+v+' ')>=0}}
function ok(c,terms,skip){{for(var k in sel){{if(k===skip)continue;var vs=Object.keys(sel[k]);if(!vs.length)continue;
var hit=false;for(var j=0;j<vs.length;j++)if(has(c,k,vs[j])){{hit=true;break}}if(!hit)return false}}
for(var t=0;t<terms.length;t++)if(c.dataset.q.indexOf(terms[t])<0)return false;return true}}
function apply(){{
var terms=q.value.trim().toLowerCase().split(/\\s+/).filter(Boolean),n=0;
cards.forEach(function(c){{var v=ok(c,terms);c.classList.toggle('hide',!v);if(v)n++}});
chipEls.forEach(function(b){{var k=b.dataset.k,v=b.dataset.v,m=0;cards.forEach(function(c){{if(has(c,k,v)&&ok(c,terms,k))m++}});
b.querySelector('i').textContent=m;b.classList.toggle('zero',!m&&!sel[k][v])}});
[].slice.call(feed.querySelectorAll('.day')).forEach(function(d){{d.hidden=!d.querySelector('article.g:not(.hide)')}});
var any=terms.length||Object.keys(sel).some(function(k){{return Object.keys(sel[k]).length}});
count.textContent=any?('显示 '+n+' / '+cards.length+' 款'):('共 '+cards.length+' 款');
clear.hidden=!any;none.hidden=n>0;
try{{history.replaceState(null,'',any?'#'+encodeURIComponent(JSON.stringify({{q:q.value,s:sel}})):location.pathname)}}catch(e){{}}
}}
function key(c){{if(sort==='heat')return -(+c.dataset.heat);if(sort==='pub')return c.dataset.date?-Date.parse(c.dataset.date):0;return 0}}
function arrange(){{
if(sort==='date'){{flat.hidden=true;feed.hidden=false;cards.forEach(function(c){{c._home.appendChild(c)}});return}}
var cs=cards.slice().sort(function(a,b){{return key(a)-key(b)}});cs.forEach(function(c){{flat.appendChild(c)}});flat.hidden=false;feed.hidden=true}}
chipEls.forEach(function(b){{b.addEventListener('click',function(){{var k=b.dataset.k,v=b.dataset.v;
if(sel[k][v])delete sel[k][v];else sel[k][v]=1;b.classList.toggle('on',!!sel[k][v]);apply()}})}});
q.addEventListener('input',apply);
clear.addEventListener('click',function(){{q.value='';for(var k in sel)sel[k]={{}};chipEls.forEach(function(b){{b.classList.remove('on')}});apply()}});
document.getElementById('sortSeg').addEventListener('click',function(ev){{var b=ev.target.closest('button');if(!b)return;
[].slice.call(this.children).forEach(function(x){{x.classList.toggle('on',x===b)}});sort=b.dataset.sort;arrange()}});
document.addEventListener('keydown',function(ev){{if(ev.key==='/'&&document.activeElement!==q){{ev.preventDefault();q.focus()}}}});
try{{if(location.hash.length>3&&location.hash.indexOf('%7B')===1){{var s=JSON.parse(decodeURIComponent(location.hash.slice(1)));q.value=s.q||'';
for(var k in s.s)if(sel[k])sel[k]=s.s[k];chipEls.forEach(function(b){{b.classList.toggle('on',!!sel[b.dataset.k][b.dataset.v])}})}}}}catch(e){{}}
apply();
var tt=document.getElementById('toTop');addEventListener('scroll',function(){{tt.classList.toggle('show',scrollY>900)}},{{passive:true}});
tt.addEventListener('click',function(){{scrollTo({{top:0,behavior:'smooth'}})}});
}})();
</script>
</body>
</html>
'''
    with open(os.path.join(out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(page)
    with open(os.path.join(out, '.nojekyll'), 'w') as f:
        f.write('')
    print(f'[feed] {date} 累计 {len(games)}，重点轮播 {len(hot)}，近 7 天入库 {n_week} → site/index.html ({len(page.encode()) // 1024} KB)')
    return {'total': len(games), 'hot': len(hot), 'week': n_week}


if __name__ == '__main__':
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None)
