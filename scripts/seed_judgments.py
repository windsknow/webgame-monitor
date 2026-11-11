# -*- coding: utf-8 -*-
"""两份 X 样例（2026-11-10、2026-11-11）粗筛命中帖子的主题判断，2026-11-11 会话逐条核验后写入。
以后每周核验时直接改 data/x_judgments.json，不需要再跑这个脚本。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitor import util

D = '2026-11-11'
def J(is_game, in_browser, note, ai='', games=None):
    return {'is_game': is_game, 'in_browser': in_browser, 'ai_evidence': ai, 'note': note,
            'games': games or [], 'judged': D, 'by': '会话核验'}

SALT = 'https://claude.ai/artifact/BGY1SztrAYcJQHSsnHHGmp'
judg = {
 # ---- 2026-11-10 ----
 '2119791071089852888': J(True, None, '开发中的僵尸游戏片段，帖子没有给试玩链接', '帖子写明用 Claude 和 three.js 氛围编程'),
 '2119695510214701106': J(True, False, '转述他人的《塞尔达》联机改版，运行在模拟器或 PC 上，不是网页游戏'),
 '2119675145627599356': J(False, None, '开源 Skill 合集，讲的是工具，不是一款具体的游戏'),
 '2119643510207099323': J(True, None, '开发进度片段，作者简介写"很快上线"，还没有公开试玩', '帖子写明 Three.js + Opus'),
 '2119579206451413467': J(True, False, 'Lichii 是 Switch 2 的 PC 模拟器（多家媒体报道），不是网页游戏'),
 '2119538130470457833': J(True, False, '用 Unity 6 制作的钓鱼游戏，不是网页游戏', '帖子写明用 Claude Opus 5.5'),
 '2119517930300076261': J(True, True, 'Dimension 9 的开发者数据帖，游戏已收录', '',
                          [{'name': 'Backrooms: Dimension 9', 'url': 'https://dimension9.space/', 'how': '作者是该游戏的开发者'}]),
 '2119513077653921992': J(False, None, '讲用 Codex 做业务系统的经历，和游戏无关'),
 '2119823772920467776': J(False, None, 'GTA 6 预载的媒体报道'),
 '2119822435444764693': J(False, None, 'GTA 6 预载的媒体报道'),
 '2119690611691577706': J(False, None, '模型评测的作品合集页，不是一款具体的游戏'),
 # ---- 2026-11-11 ----
 '2120163317889347853': J(True, True, '转述 Saltline，游戏已收录', '', [{'name': 'Saltline', 'url': SALT, 'how': '引用了作者原帖'}]),
 '2120157496686018787': J(False, None, 'Big Walk 披露使用 AI 引发争议的新闻评论'),
 '2120125184606658728': J(True, True, '开发者本人发布，帖子自带试玩链接', '帖子写明用 Claude Fable 5.2 制作，发布在 Remix',
                          [{'name': 'Banana Mine Rush', 'url': 'https://remix.gg/g/banana-mine-rush', 'how': '帖子自带链接'}]),
 '2120089768851587343': J(False, None, '对独立游戏奖项争议的评论'),
 '2120086512549458322': J(True, True, '分析 Saltline 的源文件，游戏已收录', '', [{'name': 'Saltline', 'url': SALT, 'how': '引用帖里的链接'}]),
 '2120071833617568076': J(False, None, 'Fable 5.2 发布的能力总结，不是一款具体的游戏'),
 '2120049690624635261': J(True, False, '用 Unreal 5.8 制作，不是网页游戏', '帖子写明用 Tripo、Claude、Codex'),
 '2120020469487440210': J(True, True, '帖子说是浏览器游戏，但没有给链接；作者主页的链接指向另一款作品 FNX（spawn.co/@robot/fnx）', '帖子写明用 Fable 5.2 制作'),
 '2120010936584945922': J(True, None, '篮球游戏演示片段，帖子没有给试玩链接', '帖子写明让 Fable 5.2 用 three.js 制作'),
 '2119985854450204968': J(True, True, '转述 Saltline，游戏已收录', '', [{'name': 'Saltline', 'url': SALT, 'how': '引用了作者原帖'}]),
 '2119980595279212916': J(True, False, 'Roblox 游戏，不是独立的网页游戏'),
 '2119979537320534135': J(False, None, '帖子下的读者备注指出：视频是 2007 年的《孤岛危机》画面，不是 AI 生成的游戏'),
 '2119969113762082931': J(True, None, '剑斗游戏演示片段，帖子没有说明平台，也没有试玩链接', '帖子写明由 Fable 5.2 制作'),
 '2119946035221135843': J(False, None, '对 Steam 上 AI 游戏的评论'),
 '2119935331499925557': J(True, None, '带传送门的竞技场 FPS，作者说在办公室内部对战试玩；帖子正文和公开可见的回复里都没有试玩链接', '帖子写明由 Fable 5.2 制作'),
 '2119931740940640542': J(True, None, 'MMO 世界演示片段，帖子没有给试玩链接；作者是 Spawn 上多款已收录游戏的开发者', '帖子写明用 Fable 5.2 一天做成'),
 '2119900487688622171': J(True, True, 'Gilt & Gutter，帖子说可以在浏览器里玩，但正文没有链接；作者的个人网站和 itch.io 页面也没有列出这款游戏', '帖子写明由 Claude Fable 5.2 制作'),
 '2119886664017547687': J(True, True, '作品合集帖，串内有 2 款发布在 Spawn 上的游戏；另有 1 个体素破坏引擎演示（spawn.co/@mattshumer/voxel-engine，属于技术演示，不算游戏），以及视频和机器人设计各 1 项', '串内帖子写明由 Fable 5.2 制作', [
     {'name': 'Earth', 'url': 'https://www.spawn.co/@mattshumer/earth', 'how': '串内帖子的链接',
      'source_post': 'https://x.com/mattshumer_/status/2119877065701036047', 'post_views': 398464, 'post_time_utc': '2026-11-09 19:37:32'},
     {'name': 'Hogwarts Royale', 'url': 'https://www.spawn.co/@mattshumer/hogwarts-royale', 'how': '串内帖子的链接',
      'source_post': 'https://x.com/mattshumer_/status/2119872098306810226', 'post_views': 169533, 'post_time_utc': '2026-11-09 19:17:48'}]),
 '2119868627885441389': J(True, None, '作品合集帖，串内有两段游戏演示（用 Unreal 做的射击游戏、类 Fall Guys 的多人游戏），都没有试玩链接', '帖子写明由 Fable 5.2 制作'),
 '2120191101801996669': J(False, None, '转发的引擎技术演示，不是一款游戏'),
 '2120184770936397976': J(False, None, '品牌方的产品体验页宣传，不是游戏'),
 '2120094175370551362': J(False, None, '媒体报道，讲的是模拟器上的联机改版'),
 '2119987129204047894': J(False, None, '行业评论，和游戏无关'),
}
p = util.p('data', 'x_judgments.json')
cur = util.load(p, {})
cur.update(judg)
util.save(p, cur)
print('judgments', len(cur))
