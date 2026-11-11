# -*- coding: utf-8 -*-
"""一次性导入：把《网页游戏新品观察-游戏列表.xlsx》的 68 款转成 data/games.json，并把已有的图标和截图拷进 assets/games/。

用法：python3 scripts/import_xlsx.py <游戏列表.xlsx> <报告生成脚本目录>
只读取这两个位置，不修改它们。需要 openpyxl（只有这个脚本用到）。
"""
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitor import util  # noqa: E402

DIRECTIONS = ['把真实世界做成游戏', '端游级3D 美术表现力', '游戏复刻与反编译', '多人在线交互',
              '把大模型当作玩法本身', '休闲益智与分享比拼', '独立精品与巧妙创意']
AI_PLATFORMS = {'Spawn', 'Verse8', 'Astrocade', 'Rosebud AI'}
AI_RE = re.compile(r'(Claude|Codex|Cursor|GPT|Gemini|Fable|Opus|Vibe Jam|AI 游戏精选清单|AI 辅助|由 AI|用 AI|大模型|一句提示词)', re.I)
BASELINE = '2026-11-10'
# 换过域名的游戏：旧链接和代码仓库也算同一款，去重时一并比对。
ALT_KEYS = {'fanto': ['fantos-megamart.vercel.app'], 'fm1': ['fm1.moises.cloud'],
            'tidewater': ['github.com/dgreenheck/tidewater']}
# 已知的开发者账号和首发帖（取自上一阶段的首发渠道分析），用于把 X 上的新帖对回已收录的游戏。
X_INFO = {
    'lagoslife': (['Shalom_HeyEliy'], ['https://x.com/Shalom_HeyEliy/status/2105541070486470749']),
    'pokebonk': (['ImCharcher'], ['https://x.com/ImCharcher/status/2110395400914092426']),
    'tidewater': (['dangreenheck'], ['https://x.com/dangreenheck/status/2102878170089169235',
                                    'https://x.com/dangreenheck/status/2103004432786993341']),
    'dimension9': (['apekshik'], ['https://x.com/apekshik/status/2118847014071120179',
                                  'https://x.com/apekshik/status/2119517930300076261']),
    'saltline': (['felixrieseberg'], ['https://x.com/felixrieseberg/status/2119925633564463609']),
    'sp_stranglethorn': (['Izkimar'], []), 'sp_league': (['Izkimar'], []), 'sp_diablo': (['Izkimar'], []),
}


def norm_name(s):
    return re.sub(r'[\W_]+', '', s).lower()


def main(xlsx, old_dir):
    import openpyxl
    sys.path.insert(0, old_dir)
    import data as old      # 上一阶段的数据模块，只读取 key、名称、链接
    import extra as oldx
    keymap, urlmap = {}, {}
    for g in old.GROUPS + old.PLATFORMS:
        for x in g['games']:
            keymap[norm_name(x['name'])] = x['key']
            urlmap[util.game_key(x['url'])] = x['key']
    red = getattr(oldx, 'RED_STATS', {})
    extra_shots = getattr(oldx, 'EXTRA_SHOTS', {})

    ws = openpyxl.load_workbook(xlsx).active
    rows = [r for r in list(ws.iter_rows(values_only=True))[1:] if r[2]]
    games, missing_img, hands = [], [], {}
    adir = util.p('assets', 'games')
    os.makedirs(adir, exist_ok=True)
    for r in rows:
        (no, grp, name, kind, date_text, play, hands_on, tech, stats_raw, xcheck, src, url, hot) = r[:13]
        key = keymap.get(norm_name(name)) or urlmap.get(util.game_key(url)) or util.slug(name)
        stats = []
        for line in (stats_raw or '').split('\n'):
            line = line.strip()
            if not line:
                continue
            k, _, v = line.partition('：')
            stats.append({'label': k.strip(), 'value': v.strip(), 'doubt': k.strip() in red.get(key, set())})
        blob = '；'.join(str(x or '') for x in (tech, play, src, date_text))
        m = AI_RE.search(blob)
        if grp in AI_PLATFORMS:
            ai_tag, ev = 'ai', f'发布在 AI 创作平台 {grp}'
        elif grp == '把大模型当作玩法本身':
            ai_tag, ev = 'ai', '大模型是玩法的一部分'
        elif m:
            ai_tag = 'ai'
            sent = next((s for s in re.split(r'[；。\n]', blob) if m.group(1) in s), m.group(1))
            ev = sent.strip()
        else:
            ai_tag, ev = 'unlabeled', ''
        icon = shot = None
        for ext in ('png', 'svg'):
            f = os.path.join(old_dir, 'assets', f'{key}_icon.{ext}')
            if os.path.exists(f):
                icon = f'{key}_icon.{ext}'
                shutil.copyfile(f, os.path.join(adir, icon))
                break
        f = os.path.join(old_dir, 'assets', f'{key}_shot.jpg')
        if os.path.exists(f):
            shot = f'{key}_shot.jpg'
            shutil.copyfile(f, os.path.join(adir, shot))
        else:
            missing_img.append(name)
        xs = []
        for x in extra_shots.get(key, []):
            f = os.path.join(old_dir, 'assets', x)
            if os.path.exists(f):
                shutil.copyfile(f, os.path.join(adir, x))
                xs.append(x)
        if hands_on:
            hands[key] = hands_on
        games.append({
            'id': key,
            'name': name,
            'kind': kind or '',
            'group': grp,
            'group_type': 'direction' if grp in DIRECTIONS else 'platform',
            'url': url,
            'key': util.game_key(url),
            'date_text': date_text or '',
            'stats': stats,
            'play': play or '',
            'tech': tech or '',
            'xcheck': xcheck or '',
            'src': src or '',
            'hot': hot == '是',
            'ai_tag': ai_tag,
            'ai_evidence': ev,
            'status': 'verified',
            'first_seen': {'date': BASELINE, 'channel': '基线报告'},
            'added': BASELINE,
            'issue': '基线',
            'icon': icon, 'shot': shot, 'extra_shots': xs,
            'sources': [],
            'alt_keys': ALT_KEYS.get(key, []),
            'x_handles': X_INFO.get(key, ([], []))[0],
            'source_posts': X_INFO.get(key, ([], []))[1],
        })
    util.save(util.p('data', 'games.json'), games)
    # 体验记录是内部笔记，单独存到不进仓库的目录
    util.save(util.p('data', 'private', 'hands_on.json'), hands)
    for n in ('_favicon.png', '_touch.png'):
        f = os.path.join(old_dir, 'assets', n)
        if os.path.exists(f):
            shutil.copyfile(f, util.p('assets', n.lstrip('_')))
    print('games', len(games), '| ai', sum(g['ai_tag'] == 'ai' for g in games),
          '| unlabeled', sum(g['ai_tag'] == 'unlabeled' for g in games),
          '| hot', sum(g['hot'] for g in games))
    print('无截图：', missing_img)
    print('未标注 AI 证据：', [g['name'] for g in games if g['ai_tag'] == 'unlabeled'])
    ids = [g['id'] for g in games]
    assert len(ids) == len(set(ids)), '有重复 id'


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
