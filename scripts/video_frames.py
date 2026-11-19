# -*- coding: utf-8 -*-
"""下载一段演示视频、抽帧并拼成一张对照图（每周核验时用，不进定时任务）。

python3 -I scripts/video_frames.py <视频地址或 X 帖子链接> <输出目录> [帧数=8]

X 帖子链接会先通过 fxtwitter 的公开接口拿到视频地址（不登录）。
视频存到输出目录下的 video.mp4，帧存到 frames/，对照图是 sheet.jpg（两列，每帧标时间）。
抽帧用 macOS 自带的 AVFoundation（scripts/frames.swift），拼图用 Pillow。
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

UA = {'User-Agent': 'webgame-monitor/1.0 (video frames)'}
HERE = os.path.dirname(os.path.abspath(__file__))


def video_url(u):
    m = re.search(r'(?:x|twitter)\.com/([^/]+)/status/(\d+)', u)
    if not m:
        return u, {}
    t = json.load(urllib.request.urlopen(urllib.request.Request(
        f'https://api.fxtwitter.com/{m.group(1)}/status/{m.group(2)}', headers=UA), timeout=30))['tweet']
    vids = (t.get('media') or {}).get('videos') or []
    if not vids:
        raise SystemExit('这条帖子没有视频')
    v = vids[0]
    best = max(v.get('variants') or [{'url': v['url'], 'bitrate': 0}],
               key=lambda x: (x.get('content_type', 'video/mp4') == 'video/mp4', x.get('bitrate') or 0))
    return best['url'], {'views': t.get('views'), 'likes': t.get('likes'), 'duration': v.get('duration'),
                         'created_at': t.get('created_at')}


def sheet(fdir, out):
    from PIL import Image, ImageDraw
    fs = sorted(f for f in os.listdir(fdir) if f.endswith('.jpg'))
    ims = [Image.open(os.path.join(fdir, f)) for f in fs]
    if not ims:
        raise SystemExit('没有抽到帧')
    w = 640
    ims = [im.resize((w, int(im.height * w / im.width))) for im in ims]
    h = max(im.height for im in ims)
    rows = (len(ims) + 1) // 2
    S = Image.new('RGB', (w * 2 + 12, rows * (h + 6)), 'white')
    for i, (im, f) in enumerate(zip(ims, fs)):
        x, y = (i % 2) * (w + 12), (i // 2) * (h + 6)
        S.paste(im, (x, y))
        ImageDraw.Draw(S).text((x + 8, y + 6), re.sub(r'^f\d+_0*', '', f[:-4]), fill='yellow')
    S.save(out, quality=82)


def main():
    src, out = sys.argv[1], sys.argv[2]
    n = sys.argv[3] if len(sys.argv) > 3 else '8'
    os.makedirs(out, exist_ok=True)
    url, meta = video_url(src)
    mp4 = os.path.join(out, 'video.mp4')
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, open(mp4, 'wb') as f:
        f.write(r.read(200_000_000))
    r = subprocess.run(['swift', os.path.join(HERE, 'frames.swift'), mp4, os.path.join(out, 'frames'), n],
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip()[-300:])
    sheet(os.path.join(out, 'frames'), os.path.join(out, 'sheet.jpg'))
    json.dump(dict(meta, src=src, video=url), open(os.path.join(out, 'meta.json'), 'w'), ensure_ascii=False)
    print('对照图', os.path.join(out, 'sheet.jpg'), meta)


if __name__ == '__main__':
    main()
