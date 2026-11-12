# -*- coding: utf-8 -*-
"""生成静态网站到 site/：index.html、样式、图片、日汇总和周汇总。

页面只读 data/ 下的文件，不联网。图片是单独的文件，不内嵌进页面。
"""
import datetime
import glob
import html
import os
import re
import shutil

from . import util, x_ingest
from .merge import issue_of

e = html.escape
PAL = ['#2a78d6', '#eb6834', '#0a7a0a', '#8a4fd6', '#c2410c', '#0e7490', '#b91c5c', '#4d7c0f']
AI_TAG = {'ai': ('tag-ai', 'AI 参与'), 'likely': ('tag-likely', '疑似 AI · 无直接证据'),
          'non_ai': ('tag-non', '非 AI · 创意'), 'unlabeled': ('tag-unl', 'AI 证据待确认')}
CHANNEL = {'x': 'X 帖子', 'hn': 'Hacker News', 'threejs': 'three.js 论坛', 'github': 'GitHub', 'itch': 'itch.io',
           'awesome': 'AI 游戏精选清单', 'spawn': 'Spawn 热榜', 'verse8': 'Verse8 热门', 'astrocade': 'Astrocade 热门页',
           'rosebud': 'Rosebud 首页精选', 'wavedash': 'Wavedash 热门', '基线报告': '2026-11-10 基线整理'}
LINK = {'none': ('试玩链接未找到', '看原帖 ↗'), 'repo': ('只有代码仓库', '看仓库 ↗')}
CAL = ('<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><rect x="2" y="3" width="12" height="11" rx="2" '
       'fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" stroke="currentColor" '
       'stroke-width="1.4" stroke-linecap="round"/></svg>')


def num(s):
    """把"607 万""1,447 星"这类写法取成数字，只用于排序。"""
    best = 0
    for m in re.finditer(r'(\d[\d,]*(?:\.\d+)?)\s*(亿|万)?', s or ''):
        v = float(m.group(1).replace(',', '')) * {'亿': 1e8, '万': 1e4, None: 1}[m.group(2)]
        best = max(best, v)
    return best


def heat(g):
    vals = [num(s['value']) for s in g.get('stats', [])
            if re.search(r'浏览|访问|玩家|游玩|Discord|GitHub|Hacker News|在线|日活', s['label'])]
    return int(max(vals, default=0))


def date_key(g):
    m = re.search(r'(20\d\d)-(\d\d)(?:-(\d\d))?', g.get('date_text') or '')
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3) or '01'}"
    return (g.get('added') or '') if g.get('status') == 'auto' else ''


def card(g, is_new=False):
    col = PAL[sum(map(ord, g['id'])) % len(PAL)]
    first = next((c for c in g['name'] if c.isalnum()), '?').upper()
    A = 'assets/games/'
    icon_html = (f'<img class="ico" src="{A}{e(g["icon"])}" alt="" loading="lazy">' if g.get('icon')
                 else f'<span class="ico ico-l" style="background:{col}">{e(first)}</span>')
    shot_html = (f'<img src="{A}{e(g["shot"])}" alt="{e(g["name"])} 画面" loading="lazy">' if g.get('shot')
                 else f'<div class="noshot" style="--c:{col}"><span>{e(g["name"])}</span><small>暂无画面截图</small></div>')
    stats = ''.join(
        f'<div class="st{" st-red" if s.get("doubt") else ""}"><dt>{e(s["label"])}{"（口径存疑）" if s.get("doubt") else ""}</dt>'
        f'<dd>{e(s["value"])}</dd></div>' for s in g.get('stats', []))
    xs = ''.join(f'<img src="{A}{e(x)}" alt="{e(g["name"])} 画面" loading="lazy">' for x in g.get('extra_shots', []))
    xs = f'<div class="xshots">{xs}</div>' if xs else ''
    hot = '<span class="hot">★ 重点</span>' if g.get('hot') else ''
    tags = []
    if is_new:
        tags.append('<span class="tag tag-new">本期新增</span>')
    cls, lab = AI_TAG.get(g.get('ai_tag') or 'unlabeled', AI_TAG['unlabeled'])
    tags.append(f'<span class="tag {cls}" title="{e(g.get("ai_evidence") or "")}">{lab}</span>')
    if g.get('link_status') in LINK:
        tags.append(f'<span class="tag tag-nolink">{LINK[g["link_status"]][0]}</span>')
    if g.get('status') == 'auto':
        tags.append('<span class="tag tag-auto" title="按规则自动入库，介绍文字尚未经过试玩核验">自动入库 · 待核验</span>')
    if g.get('play'):
        p = g['play']
        play = ('<b class="idea">创意点</b>' + e(p[4:])) if p.startswith('创意点：') else e(p)
        play = f'<p class="play">{play}</p>'
    elif g.get('quote'):
        play = f'<p class="play quote"><b>{e(g.get("quote_src") or "原文")}（原文）</b>{e(html.unescape(g["quote"]))}</p>'
    else:
        play = '<p class="play"></p>'
    tech = f'<b>技术</b>{e(g["tech"])}' if g.get('tech') else ''
    note = f'<b>交叉验证</b>{e(g["xcheck"])}' if g.get('xcheck') else ''
    fs = g.get('first_seen') or {}
    found = ''
    if fs.get('channel') and fs['channel'] != '基线报告':
        found = f'首次发现：{e(fs["date"])} · {e(CHANNEL.get(fs["channel"], fs["channel"]))}'
    why = ''
    if g.get('reasons') or g.get('signals'):
        bits = list(g.get('reasons') or [])
        hot_bits = [f"{s['类别']}：{s['说明']}" for s in g.get('signals') or [] if s['类别'] != '技术']
        if hot_bits:
            bits.append('标为重点的依据：' + '；'.join(hot_bits))
        if g.get('ai_evidence'):
            bits.append('AI 证据：' + g['ai_evidence'])
        why = '<b>入库理由</b>' + e('；'.join(bits))
    src_txt = (g.get('src') or '') + (f"（数据截至 {g['stats_date']}）" if g.get('stats_date') else '')
    go = LINK.get(g.get('link_status'), ('', '打开 ↗'))[1]
    srcs = ''
    if g.get('sources'):
        seen, links = set(), []
        for s in g['sources']:
            if s['url'] in seen:
                continue
            seen.add(s['url'])
            links.append(f'<a href="{e(s["url"])}" target="_blank" rel="noopener">{e(CHANNEL.get(s["channel"], s["channel"]))} ↗</a>')
        srcs = f'<span class="srcs">{"".join(links[:6])}</span>'
    return f'''<article class="g card{' is-hot' if g.get('hot') else ''}" id="{'new-' if is_new else ''}{e(g['id'])}" data-date="{date_key(g)}" data-heat="{heat(g)}" data-ai="{e(g.get('ai_tag') or 'unlabeled')}" data-hot="{1 if g.get('hot') else 0}">
<div class="media"><a class="shot" href="{e(g['url'])}" target="_blank" rel="noopener">{shot_html}{hot}</a>{xs}</div>
<header class="gh">{icon_html}<div><h4>{e(g['name'])}</h4><span class="kind">{e(g.get('kind') or '')}</span></div></header>
<div class="tags">{''.join(tags)}</div>
<p class="date">{CAL}{e(g.get('date_text') or '')}</p>
<p class="found">{found}</p>
<dl class="stats">{stats}</dl>
{play}
<p class="tech">{tech}</p>
<p class="why">{why}</p>
<p class="note">{note}</p>
<footer class="gf"><span class="src" title="{e(src_txt)}">数据来源：{e(src_txt)}{srcs}</span><a class="go" href="{e(g['url'])}" target="_blank" rel="noopener">{go}</a></footer>
</article>'''


def is_full(g):
    """用完整卡片展示：核验过的、重点的、已经写了玩法介绍的。其余自动入库的用紧凑列表。"""
    return g.get('status') != 'auto' or g.get('hot') or bool(g.get('play'))


def compact_table(games, new_ids=(), prefix=''):
    """自动入库、还没核验的游戏：一行一款，只放名称、标签、数字和出处。"""
    rows = []
    for g in sorted(games, key=lambda g: -heat(g)):
        fs = g.get('first_seen') or {}
        tag = AI_TAG.get(g.get('ai_tag') or 'unlabeled', AI_TAG['unlabeled'])
        tags = f'<span class="tag {tag[0]}" title="{e(g.get("ai_evidence") or "")}">{tag[1]}</span>'
        if g.get('link_status') in LINK:
            tags += f' <span class="tag tag-nolink">{LINK[g["link_status"]][0]}</span>'
        if g['id'] in new_ids:
            tags = '<span class="tag tag-new">本期新增</span> ' + tags
        stats = '；'.join(f"{s['label']} {s['value']}" + ('（口径存疑）' if s.get('doubt') else '') for s in g.get('stats', []))
        seen, links = set(), []
        for s_ in g.get('sources') or []:
            if s_['url'] not in seen:
                seen.add(s_['url'])
                links.append(f'<a href="{e(s_["url"])}" target="_blank" rel="noopener">{e(CHANNEL.get(s_["channel"], s_["channel"]))}</a>')
        q = html.unescape((g.get('quote') or '').strip())
        q = f'<span class="q">{e(q[:110] + ("…" if len(q) > 110 else ""))}</span>' if q else ''
        rows.append(
            f'<tr id="{prefix}{e(g["id"])}"><td class="on"><a href="{e(g["url"])}" target="_blank" rel="noopener">{e(g["name"])}</a>'
            f'{q}</td><td>{tags}</td>'
            f'<td class="os">{e(fs.get("date") or "")}<br>{e(CHANNEL.get(fs.get("channel"), fs.get("channel") or ""))}</td>'
            f'<td>{e(stats)}</td><td>{"　".join(links[:4])}</td></tr>')
    return ('<div class="card tblwrap wt cmp"><table><thead><tr><th>名称</th><th>标签</th><th>首次发现</th><th>数据</th>'
            '<th>出处</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')


def group_section(meta, games, n, new_ids=()):
    full = sorted([g for g in games if is_full(g)], key=lambda g: (not g.get('hot'),))
    rest = [g for g in games if not is_full(g)]
    cards = '\n'.join(card(g) for g in full)
    nh = sum(1 for g in games if g.get('hot'))
    cnt = f'{len(games)} 款' + (f'，其中重点 {nh} 款' if nh else '')
    body = f'<div class="cards">{cards}</div>' if full else ''
    if rest:
        body += f'<h3 class="gt">自动入库、等待核验的 {len(rest)} 款</h3>' + compact_table(rest, new_ids)
    return (f'<div class="grp" id="{meta["id"]}"><div class="grp-h"><span class="num">{n}</span><div>'
            f'<h3>{e(meta["title"])}<span class="cnt">{cnt}</span></h3><p>{e(meta["sub"])}</p></div></div>'
            f'{body}</div>')


def lead_metric(l):
    m = l.get('metrics') or {}
    if l['channel'] == 'x' and m.get('x_views'):
        return f"浏览 {util.fmt_num(m['x_views'])}"
    if l['channel'] == 'hn':
        return f"{m.get('hn_points')} 分 / {m.get('hn_comments') or 0} 评论"
    if l['channel'] == 'threejs':
        return f"{util.fmt_num(m.get('threejs_views'))} 次浏览"
    if l['channel'] == 'github':
        return f"{util.fmt_num(m.get('github_stars'))} 星"
    for k in ('spawn_plays', 'astrocade_plays', 'rosebud_plays'):
        if m.get(k):
            return f"游玩 {util.fmt_num(m[k])} 次"
    return ''


def watch_tables(cands, date, issue):
    rows = []
    for c in sorted(cands.values(), key=lambda c: c['first_seen']['date'], reverse=True):
        if c['status'] != '观察':
            continue
        srcs = '　'.join(f'<a href="{e(l["source_url"])}" target="_blank" rel="noopener">{e(CHANNEL.get(l["channel"], l["channel"]))}</a>'
                        f' <span class="m">{e(lead_metric(l))}</span>' for l in c['leads'])
        tag = AI_TAG.get(c.get('ai_tag') or 'unlabeled')[1]
        rows.append(f'<tr><td class="on"><a href="{e(c["url"])}" target="_blank" rel="noopener">{e(c["name"] or c["key"])}</a></td>'
                    f'<td class="os">{e(c["first_seen"]["date"])}</td><td>{srcs}</td><td>{e(tag)}</td>'
                    f'<td>{e("；".join(c.get("reasons") or []))}</td></tr>')
    t1 = ('<div class="card tblwrap wt"><table><thead><tr><th>名称</th><th>首次发现</th><th>来源与数据</th><th>AI 标签</th>'
          '<th>现状</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>') if rows else \
        '<div class="card empty">目前没有处于观察中的线索。</div>'
    # X 上像是在讲游戏、但还不能确定是一款网页游戏的帖子（本期内）
    xr = []
    judg = util.load(util.p('data', 'x_judgments.json'), {})
    for path in sorted(glob.glob(util.p('raw', 'x', '*.json')), reverse=True):
        raw = util.load(path)
        if issue_of(raw['date']) != issue or raw['date'] > date:
            continue
        for it in raw['items']:
            j = judg.get(it['tweet_id']) or {}
            verdict, _ = x_ingest.judge(it, j)
            if verdict != 'maybe' or x_ingest.linkless_ok(it, j):
                continue                             # 能入库的无链接帖子已经在收录库里了
            note = j.get('note') or '；'.join(it.get('rule_reasons') or [])
            v = (it.get('game') or {}).get('views_now') or it.get('views')
            xr.append((v or 0, f'<tr><td class="on"><a href="{e(it["link"])}" target="_blank" rel="noopener">@{e(it["handle"])}</a></td>'
                               f'<td class="num">{e(util.fmt_num(v))}</td><td>{e(note)}</td>'
                               f'<td>{e(j.get("ai_evidence") or "")}</td></tr>'))
    xr.sort(reverse=True)
    t2 = ('<div class="card tblwrap wt"><table><thead><tr><th>来源帖子</th><th>帖子浏览</th><th>说明</th><th>AI 证据</th></tr></thead>'
          '<tbody>' + ''.join(r for _, r in xr) + '</tbody></table></div>') if xr else \
        '<div class="card empty">本期没有这类帖子。</div>'
    return t1, t2, len(rows), len(xr)


def method_html(cfg):
    F, H = cfg['thresholds']['floors'], cfg['thresholds']['hot']
    low = (F.get('chart_min_plays') or {}).get('astrocade')
    ch = [
        ('X 帖子数据', '每日帖子数据。先按关键词和链接初筛，再判断是不是在讲一款能在浏览器里玩的游戏，然后顺着帖子找试玩链接',
         '判断为网页游戏的都入库；没找到试玩链接的，以来源帖子入库'),
        ('Hacker News', '标题、链接、分数、评论数', f'7 天内 ≥ {F["hn_points"]} 分，标题像在讲游戏'),
        ('three.js 论坛作品区', '标题、链接、浏览数、首帖内容', f'一周内 ≥ {F["threejs_views_week"]} 次浏览，内容像在讲游戏或写明 AI 参与'),
        ('GitHub', '仓库名、描述、星数、主页链接', f'创建 30 天内 ≥ {F["github_stars_30d"]} 星，有试玩主页，并且不是引擎或框架'),
        ('itch.io', '网页平台的新品与热门榜', f'连续 {F["itch_weeks_on_chart"]} 周在榜，或描述写明 AI 参与'),
        ('AI 游戏精选清单', 'awesome-ai-built-games 的新增条目', '新增的都入库'),
        ('Spawn 热榜', '前 %d 名的游戏名、游玩数、发布日期' % F['spawn_top_n'], '新进榜的；首次读取时只取近 30 天发布的'),
        ('Verse8 热门', '趋势和游玩数两个排序的前 %d 名' % F['chart_top_n'], '新进榜的；首次读取时只取近 30 天发布的'),
        ('Astrocade 热门页', '游戏名、游玩数、发布日期',
         (f'游玩 ≥ {util.fmt_num(low)} 次并且新进榜的' if low else '新进榜的') + '；首次读取时只取近 30 天发布的'),
        ('Rosebud 首页精选', '游戏名、游玩数', '新进榜的'),
        ('Wavedash 热门', '游戏名、实时在线人数', '新进榜的'),
    ]
    rows = ''.join(f'<tr><td class="on">{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td></tr>' for a, b, c in ch)
    return f'''<div class="method">
<div class="step card"><span class="n">1</span><h3>多渠道发现</h3><p>每天从下表的渠道读取线索，过了入库线的按试玩链接合并去重。已经收录过的游戏和它们的仿冒域名会被剔除。</p></div>
<div class="step card"><span class="n">2</span><h3>入库条件</h3><ul><li>是一款网页游戏：链接在已知的游戏平台上，或者页面里能找到画布或游戏引擎；没有试玩链接的 X 帖子，要带视频或图片并且正文在讲游戏</li><li>首次公开在近 {cfg["window"]["new_days"]} 天内，或近 {cfg["window"]["reheat_days"]} 天有新的热度</li><li>不要求传播数据或游玩次数</li></ul></div>
<div class="step card"><span class="n">3</span><h3>标为重点</h3><p>达到下面任意一条：</p><ul><li>X 单帖浏览 ≥ {util.fmt_num(H["x_views"])}</li><li>Hacker News ≥ {H["hn_points"]} 分</li><li>GitHub ≥ {util.fmt_num(H["github_stars"])} 星</li><li>three.js 论坛 ≥ {util.fmt_num(H["threejs_views"])} 次浏览</li><li>平台显示游玩 ≥ {util.fmt_num(H["plays"])} 次（Verse8 的游玩数口径存疑，不计入）</li><li>同时有 Steam 页面</li></ul><p>X 来源帖的浏览数在发帖后 {cfg["thresholds"].get("x_refresh_days", 14)} 天内每天重读。2026-11-10 之前收录的 68 款，重点是逐款判断的。</p></div>
<div class="step card"><span class="n">4</span><h3>标签含义</h3><ul><li><b>AI 参与</b>：作者自述、帖子写明模型或工具、发布在 AI 创作平台，或大模型本身是玩法</li><li><b>疑似 AI</b>：没有直接证据，但符合短时间、低成本、高完成度的共性</li><li><b>非 AI · 创意</b>：明确不是 AI 制作，因创意收录</li><li><b>AI 证据待确认</b>：还没有找到证据，也没有排除</li><li><b>自动入库 · 待核验</b>：按规则自动入库，还没有经过试玩核验</li><li><b>试玩链接未找到</b>：来源帖子展示了游戏，但没有公开的试玩链接，能否在浏览器里玩还没有确认</li></ul></div>
</div>
<div class="card tblwrap" style="margin-top:14px"><table><thead><tr><th>渠道</th><th>读取内容</th><th>入库线</th></tr></thead><tbody>{rows}</tbody></table></div>'''


def digest_text(kind, date, issue, new_games, n_total, watch_names, base_url, new_hot=()):
    rng = issue_range(issue)
    word = '本期' if kind == 'weekly' else '今日'
    head = f'【AI 网页游戏新品监测】{"周汇总 " + rng if kind == "weekly" else "日汇总 " + date}'
    hot = [g for g in new_games if g.get('hot')]
    rest = sorted([g for g in new_games if not g.get('hot')], key=lambda g: -heat(g))
    lines = [head, f'累计收录 {n_total} 款；{word}新增 {len(new_games)} 款，其中重点 {len(hot)} 款。']

    def line(g, mark):
        st = '，'.join(f"{s['label']} {s['value']}" for s in g.get('stats', [])[:2])
        tag = AI_TAG.get(g.get('ai_tag') or 'unlabeled')[1]
        extra = '，' + LINK[g['link_status']][0] if g.get('link_status') in LINK else ''
        return f"{mark} {g['name']}（{tag}{extra}）{st}  {g['url']}"
    for g in hot:
        lines.append(line(g, '★'))
    old_hot = [g for g in new_hot if g not in new_games]
    if old_hot:
        lines.append(f'{word}升为重点的已入库游戏：')
        lines += [line(g, '★') for g in old_hot]
    if rest:
        lines.append(f'其他新入库 {len(rest)} 款：')
        lines += [line(g, '·') for g in rest[:12]]
        if len(rest) > 12:
            lines.append(f'  另有 {len(rest) - 12} 款见完整页面。')
    if watch_names:
        lines.append('观察中：' + '、'.join(watch_names[:8]) + ('等' if len(watch_names) > 8 else ''))
    if base_url:
        lines.append('完整页面：' + base_url)
    return '\n'.join(lines) + '\n'


def issue_range(issue):
    d = datetime.date.fromisoformat(issue)
    return f'{d.isoformat()} 至 {(d + datetime.timedelta(days=6)).isoformat()}'


def run(date=None):
    cfg = util.config()
    date = date or util.today()
    games = util.load(util.p('data', 'games.json'), [])
    cands = util.load(util.p('data', 'candidates.json'), {})
    st = util.load(util.p('data', 'static.json'), {})
    issue = issue_of(date)
    out = util.p('site')
    os.makedirs(os.path.join(out, 'assets'), exist_ok=True)
    shutil.copyfile(util.p('templates', 'style.css'), os.path.join(out, 'assets', 'style.css'))
    if os.path.isdir(os.path.join(out, 'assets', 'games')):
        shutil.rmtree(os.path.join(out, 'assets', 'games'))
    shutil.copytree(util.p('assets', 'games'), os.path.join(out, 'assets', 'games'))
    for n in ('favicon.png', 'touch.png'):
        if os.path.exists(util.p('assets', n)):
            shutil.copyfile(util.p('assets', n), os.path.join(out, 'assets', n))

    new_games = sorted([g for g in games if g.get('issue') == issue], key=lambda g: (not g.get('hot'), -heat(g)))
    today_games = [g for g in games if g.get('added') == date and g.get('issue') != '基线']
    by_title = {m['title']: m for m in st['groups']}
    grouped = {m['id']: [] for m in st['groups']}
    for g in games:
        m = by_title.get(g['group']) or by_title['待归类']
        grouped[m['id']].append(g)
    dirs = [m for m in st['groups'] if not m.get('platform') and m['id'] != 'pending']
    plats = [m for m in st['groups'] if m.get('platform')]
    dir_html = '\n'.join(group_section(m, grouped[m['id']], i + 1) for i, m in enumerate(dirs) if grouped[m['id']])
    plat_html = '\n'.join(group_section(m, grouped[m['id']], f'P{i + 1}') for i, m in enumerate(plats) if grouped[m['id']])
    pend_html = group_section(by_title['待归类'], grouped['pending'], '新', {g['id'] for g in new_games}) if grouped['pending'] else ''
    t1, t2, n_watch, n_x = watch_tables(cands, date, issue)
    new_full = [g for g in new_games if is_full(g)]
    new_rest = [g for g in new_games if not is_full(g)]
    n_new_hot = sum(1 for g in new_games if g.get('hot'))
    new_html = ''
    if new_full:
        new_html += '<div class="cards">' + '\n'.join(card(g, True) for g in new_full) + '</div>'
    if new_rest:
        new_html += (f'<h3 class="gt">其他新入库的 {len(new_rest)} 款</h3><p class="sub">按规则自动入库、还没有核验的游戏，'
                     '按数据从高到低排列。达到重点线后会升为上面的卡片。</p>') + compact_table(new_rest, prefix='new-')
    if not new_games:
        new_html = '<div class="card empty">本期还没有新增的游戏。</div>'
    side_dir = ''.join(f'<a class="s2" href="#{m["id"]}"><span>{i + 1}. {e(m["title"])}</span><i>{len(grouped[m["id"]])}</i></a>'
                       for i, m in enumerate(dirs) if grouped[m['id']])
    side_plat = ''.join(f'<a class="s2" href="#{m["id"]}"><span>{e(m["title"])}</span><i>{len(grouped[m["id"]])}</i></a>'
                        for m in plats if grouped[m['id']])
    side_pend = f'<a class="s2" href="#pending"><span>待归类</span><i>{len(grouped["pending"])}</i></a>' if grouped['pending'] else ''
    trends = ''.join(f'<li><em class="per">{e(t["period"])}</em><b>{e(t["title"])}</b><span>{e(t["text"])}</span></li>' for t in st['trends'])
    xc = st['xcheck']
    xc_rows = ''.join(
        f'<tr class="x-{r["level"]}"><td class="on">{e(r["cells"][0])}</td><td>{e(r["cells"][1])}</td><td class="num">{e(r["cells"][2])}</td>'
        f'<td><span class="lv lv-{r["level"]}">{e(r["cells"][3])}</span></td><td>{e(r["cells"][4])}</td></tr>' for r in xc['rows'])
    oth_rows = ''.join(f'<tr><td class="on">{e(r["cells"][0])}</td><td>{e(r["cells"][1])}</td><td class="os">{e(r["cells"][2])}</td></tr>'
                       for r in st['others']['rows'])
    n_hot = sum(1 for g in games if g.get('hot'))
    n_ai = sum(1 for g in games if g.get('ai_tag') == 'ai')
    title = cfg['site']['title']
    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{e(title)}</title>
<link rel="icon" type="image/png" href="assets/favicon.png">
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="top"><div class="wrap">
<div class="eyebrow">{e(title)} · 本期 {issue_range(issue)} · 数据截至 {date}</div>
<h1>{e(title)}</h1>
<p class="lede">持续跟踪新出现的、能在浏览器里直接玩的 AI 游戏。每天从 X、Hacker News、three.js 论坛、GitHub、itch.io 和各 AI 创作平台的热门页读取线索，判断为网页游戏的直接入库，达到重点线的单独标出。每款游戏都给出日期、数据和来源。</p>
<div class="kpis">
<div class="kpi card"><div class="v">{len(games)}</div><div class="l">累计收录</div><div class="n">其中重点 {n_hot} 款</div></div>
<div class="kpi card"><div class="v">{len(new_games)}</div><div class="l">本期新增</div><div class="n">其中重点 {n_new_hot} 款</div></div>
<div class="kpi card"><div class="v">{n_ai}</div><div class="l">有 AI 参与证据</div><div class="n">占累计收录的 {round(n_ai * 100 / max(len(games), 1))}%</div></div>
<div class="kpi card"><div class="v">{n_watch + n_x}</div><div class="l">线索观察</div><div class="n">{n_watch} 条待确认是不是游戏，{n_x} 条 X 帖子待判断</div></div>
</div>
</div></header>

<div class="layout">
<aside class="side" id="side"><div class="side-in">
<div class="side-t">目录<button type="button" id="tocClose" aria-label="关闭目录">×</button></div>
<a class="s1" href="#new">本期新增<i>{len(new_games)}</i></a>
<a class="s1" href="#watch">线索观察<i>{n_watch + n_x}</i></a>
<a class="s1" href="#all">累计收录<i>{len(games)}</i></a>
{side_pend}
{side_dir}
<a class="s1" href="#plats">创作平台与赛事</a>
{side_plat}
<a class="s1" href="#trends">趋势</a>
<div class="side-sep">附录</div>
<a class="s1 ap" href="#method">A　筛选方法</a>
<a class="s1 ap" href="#xcheck">B　数据交叉验证</a>
<a class="s1 ap" href="#others">C　其他线索</a>
<button id="themeBtn" type="button">深色模式</button>
</div></aside>

<main>
<section id="new">
<div class="issue-h"><h2>本期新增</h2><span class="rng">{issue_range(issue)}，共 {len(new_games)} 款，其中重点 {n_new_hot} 款</span></div>
<p class="sub">这一期新入库的游戏。重点游戏和已经写了介绍的用卡片展示，排在前面；其余的列在下面的表里。带"自动入库"标记的还没有经过试玩核验，文字取自来源帖子和游戏页面。</p>
{new_html}
</section>

<section id="watch">
<h2>线索观察</h2>
<p class="sub">热度已经到重点线、但还不能确定是不是一款网页游戏的线索，等人工确认。</p>
{t1}
<h3 class="gt">X 上像是在讲游戏、还不能确定的帖子</h3>
<p class="sub">例如多个演示的合集帖。能确定是一款游戏的，即使没有试玩链接也已经入库。</p>
{t2}
</section>

<section id="all">
<h2>累计收录</h2>
<p class="sub">全部已收录的游戏，按七个方向分组。可以切换排序，或只看某一类标签。</p>
<div class="ctl"><span>排序</span><span class="seg" id="sortSeg"><button type="button" data-sort="default" class="on">重点优先</button><button type="button" data-sort="date">按时间</button><button type="button" data-sort="heat">按热度</button></span>
<span>筛选</span><span class="seg" id="filtSeg"><button type="button" data-f="all" class="on">全部</button><button type="button" data-f="ai">AI 参与</button><button type="button" data-f="unlabeled">AI 证据待确认</button><button type="button" data-f="hot">重点</button></span></div>
{pend_html}
{dir_html}
</section>

<section id="plats">
<h2>创作平台与赛事里的热门游戏</h2>
<p class="sub">AI 创作平台、高品质网页游戏平台和赛事内部的头部作品。</p>
{plat_html}
</section>

<section id="trends">
<h2>趋势</h2>
<p class="sub">2026 年 4 月到 11 月观察到的五个趋势。</p>
<ol class="pat trends">{trends}</ol>
</section>

<div class="appx">
<div class="appx-h">附录</div>
<section id="method">
<h2><span class="apn">A</span>这些游戏是怎么筛出来的</h2>
<p class="sub">网页游戏的新品大多发布在自己的域名上，不经过应用商店，没有哪一个榜单能覆盖。这里用"多渠道发现、统一标准筛选、逐项核实数据"的方式整理。</p>
{method_html(cfg)}
</section>
<section id="xcheck">
<h2><span class="apn">B</span>数据交叉验证</h2>
<p class="sub">{e(xc['sub'])}</p>
<div class="card tblwrap"><table><thead><tr>{''.join(f'<th>{e(h)}</th>' for h in xc['head'])}</tr></thead><tbody>{xc_rows}</tbody></table></div>
</section>
<section id="others">
<h2><span class="apn">C</span>其他线索</h2>
<p class="sub">{e(st['others']['sub'])}</p>
<div class="card tblwrap"><table><thead><tr><th>名称</th><th>说明</th><th>信息来源</th></tr></thead><tbody>{oth_rows}</tbody></table></div>
</section>
</div>

<footer class="end">
<b>口径说明</b>
<ul>
<li>2026-11-10 之前收录的 68 款，数字的读取时间为 2026-11-10；之后入库的游戏，读取时间写在各自的"数据来源"里。网页游戏热度变化很快，数字会迅速过时。</li>
<li>2026-11-12 起，判断为网页游戏的线索直接入库，不再要求传播数据。自动入库的条目没有经过试玩；标着"试玩链接未找到"的，能否在浏览器里玩还没有确认。</li>
<li>开发者自己公布的数据会标明是自述；口径存疑的数据标红。</li>
<li>同人作品和移植作品与原版权方无关，可能随时下线。</li>
<li>没有对每款游戏做完整试玩，玩法介绍综合自游戏页面、开发者说明和公开报道。</li>
<li>画面与图标来自各游戏的官方页面、开源仓库或演示视频，版权归各自作者所有。</li>
</ul>
</footer>
</main>
</div>
<button type="button" id="tocBtn" aria-label="打开目录"><svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M3 5h14M3 10h14M3 15h9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>目录</button>
<script>
(function(){{var b=document.getElementById('themeBtn'),r=document.documentElement;
function cur(){{return r.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light')}}
function lab(){{b.textContent=cur()==='dark'?'浅色模式':'深色模式'}}
b.addEventListener('click',function(){{r.setAttribute('data-theme',cur()==='dark'?'light':'dark');lab()}});lab()}})();
(function(){{
var side=document.getElementById('side'),box=side.querySelector('.side-in');
var links=[].slice.call(side.querySelectorAll('a[href^="#"]'));
var targets=links.map(function(a){{return document.getElementById(a.getAttribute('href').slice(1))}});
function close(){{document.body.classList.remove('toc-open')}}
document.getElementById('tocBtn').addEventListener('click',function(){{document.body.classList.add('toc-open')}});
document.getElementById('tocClose').addEventListener('click',close);
links.forEach(function(a){{a.addEventListener('click',close)}});
var ticking=false;
function upd(){{ticking=false;var y=window.scrollY+140,best=-1,bestTop=-1;
targets.forEach(function(t,i){{if(!t)return;var top=t.getBoundingClientRect().top+window.scrollY;if(top<=y&&top>=bestTop){{bestTop=top;best=i}}}});
links.forEach(function(a,i){{a.classList.toggle('on',i===best)}});}}
window.addEventListener('scroll',function(){{if(!ticking){{ticking=true;requestAnimationFrame(upd)}}}},{{passive:true}});upd();}})();
(function(){{
var grids=[].slice.call(document.querySelectorAll('#all .cards, #plats .cards'));
grids.forEach(function(g){{[].slice.call(g.children).forEach(function(c,i){{c.dataset.i=i}})}});
function seg(id,fn){{var s=document.getElementById(id);s.addEventListener('click',function(ev){{var b=ev.target.closest('button');if(!b)return;
[].slice.call(s.children).forEach(function(x){{x.classList.toggle('on',x===b)}});fn(b)}})}}
seg('sortSeg',function(b){{var k=b.dataset.sort;grids.forEach(function(g){{var cs=[].slice.call(g.children);
cs.sort(function(a,c){{if(k==='date')return (c.dataset.date||'').localeCompare(a.dataset.date||'');
if(k==='heat')return (+c.dataset.heat)-(+a.dataset.heat);return (+a.dataset.i)-(+c.dataset.i)}});
cs.forEach(function(c){{g.appendChild(c)}})}})}});
seg('filtSeg',function(b){{var f=b.dataset.f;grids.forEach(function(g){{[].slice.call(g.children).forEach(function(c){{
var ok=f==='all'||(f==='hot'?c.dataset.hot==='1':c.dataset.ai===f);c.classList.toggle('hide',!ok)}})}})}});
}})();
</script>
</body>
</html>
'''
    with open(os.path.join(out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(page)
    # 汇总文本：方便直接推送
    dg = os.path.join(out, 'digest')
    os.makedirs(dg, exist_ok=True)
    base = cfg['site'].get('base_url') or ''
    watch_names = [c['name'] for c in cands.values() if c['status'] == '观察']
    weekly = digest_text('weekly', date, issue, new_games, len(games), watch_names, base,
                         [g for g in games if issue_of(g.get('hot_since') or '2000-01-03') == issue])
    daily = digest_text('daily', date, issue, today_games, len(games), watch_names, base,
                        [g for g in games if g.get('hot_since') == date])
    for name, txt in ((f'weekly-{issue}.txt', weekly), (f'daily-{date}.txt', daily),
                      ('weekly-latest.txt', weekly), ('daily-latest.txt', daily)):
        with open(os.path.join(dg, name), 'w', encoding='utf-8') as f:
            f.write(txt)
    os.makedirs(util.p('digest'), exist_ok=True)
    for name, txt in ((f'weekly-{issue}.txt', weekly), (f'daily-{date}.txt', daily)):
        with open(util.p('digest', name), 'w', encoding='utf-8') as f:
            f.write(txt)
    with open(os.path.join(out, '.nojekyll'), 'w') as f:
        f.write('')
    print(f'[site] {date} 累计 {len(games)}，本期新增 {len(new_games)}，观察 {n_watch}，待找链接 {n_x} → site/index.html '
          f'({len(page.encode()) // 1024} KB)')
    return {'total': len(games), 'new': len(new_games), 'watch': n_watch, 'x_pending': n_x}


if __name__ == '__main__':
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None)
