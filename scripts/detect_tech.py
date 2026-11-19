# -*- coding: utf-8 -*-
"""按站点代码特征识别技术栈（每周核验时用，不进定时任务）。

python3 -I scripts/detect_tech.py <games.json> <输出.json> <起始入库日期> <结束入库日期>

读每张卡片的试玩页面和它引用的前 8 个脚本，只做文本匹配，不执行任何下载的代码。
AI 创作平台（Astrocade、Spawn、Verse8、Wavedash、Remix）和 X 帖子跳过，这些用平台说明或作者自述。
结果给每周会话写 tech 和 tech_basis 用，依据写"站点代码特征识别"。
"""
import concurrent.futures as cf
import json
import re
import sys
import urllib.parse
import urllib.request

SKIP = ('astrocade.com', 'x.com', 'spawn.co', 'verse8.io', 'wavedash.com', 'remix.gg')
UA = {'User-Agent': 'webgame-monitor/1.0 (tech survey)'}
SIG = [
    ('three.js', r'THREE\.REVISION|three\.module|from\s*["\']three["\']|three@0\.\d+|three\.min\.js|WebGLRenderer'),
    ('Babylon.js', r'BABYLON\.|babylonjs'), ('PlayCanvas', r'playcanvas|pc\.Application'),
    ('Phaser', r'Phaser\.(Game|AUTO)|phaser(\.min)?\.js'), ('PixiJS', r'PIXI\.|pixi(\.min)?\.js'),
    ('Unity WebGL', r'createUnityInstance|UnityLoader'), ('Godot', r'godot\.js|GODOT_CONFIG|new Engine\('),
    ('WebAssembly', r'\.wasm["\']|WebAssembly\.instantiate'), ('WebGPU', r'navigator\.gpu'),
    ('Canvas 2D', r'getContext\(\s*["\']2d["\']'),
    ('Rapier 物理', r'rapier'), ('cannon 物理', r'cannon(-es)?'), ('Ammo 物理', r'Ammo\('),
    ('socket.io 联机', r'socket\.io'), ('WebRTC 点对点', r'RTCPeerConnection|peerjs|trystero'),
    ('WebSocket', r'new WebSocket\('), ('Colyseus 联机', r'colyseus'), ('Supabase', r'supabase'), ('Firebase', r'firebase'),
    ('Web Audio', r'AudioContext'),
]


def get(u, n=4_000_000):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20)
        return r.read(n).decode('utf-8', 'replace'), r.geturl()
    except Exception as ex:                         # noqa: BLE001  打不开的页面记为空
        return '', str(ex)[:80]


def detect(g):
    u = g['url']
    if any(h in urllib.parse.urlparse(u).netloc for h in SKIP):
        return g['id'], None
    page, final = get(u)
    srcs = re.findall(r'<(?:script|iframe)[^>]+src=["\']([^"\']+)', page)[:8]
    texts = [page] + [get(urllib.parse.urljoin(final if final.startswith('http') else u, s))[0] for s in srcs]
    txt = '\n'.join(texts)
    rev = (re.search(r'REVISION\s*=\s*["\'](\d{3})', txt) or re.search(r'three@0\.(\d{3})', txt)
           or re.search(r'three\.js/r(\d{3})', txt))
    return g['id'], {'url': u, 'bytes': len(page), 'scripts': srcs, 'found': [n for n, p in SIG if re.search(p, txt)],
                     'three_r': rev.group(1) if rev else None}


def main():
    games, out, d0, d1 = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    wk = [g for g in json.load(open(games, encoding='utf-8')) if d0 <= g.get('added', '') <= d1]
    with cf.ThreadPoolExecutor(8) as ex:
        res = {k: v for k, v in ex.map(detect, wk) if v}
    json.dump(res, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for k, v in res.items():
        print(k, v['bytes'], v['found'], v['three_r'])


if __name__ == '__main__':
    main()
