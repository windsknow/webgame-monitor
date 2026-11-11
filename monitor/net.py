# -*- coding: utf-8 -*-
"""取数工具：全部走 curl（本机的 urllib 请求失败过；GitHub Actions 上也自带 curl）。"""
import json
import os
import subprocess
import time

UA = 'webgame-monitor/1.0 (+curl)'


def get(url, binary=False, maxb=8_000_000, timeout=25, headers=None, retries=1):
    """GET，跟随跳转。失败返回空串/空字节，不抛异常。"""
    cmd = ['curl', '-sL', '-m', str(timeout), '--max-filesize', str(maxb), '-A', UA]
    for k, v in (headers or {}).items():
        cmd += ['-H', f'{k}: {v}']
    cmd.append(url)
    out = b''
    for i in range(retries + 1):
        r = subprocess.run(cmd, capture_output=True)
        out = r.stdout
        if out:
            break
        time.sleep(1.5 * (i + 1))
    return out if binary else out.decode('utf-8', 'ignore')


def getj(url, headers=None, timeout=25, retries=1):
    """GET 并解析 JSON；失败返回 None。"""
    try:
        return json.loads(get(url, headers=headers, timeout=timeout, retries=retries))
    except Exception:
        return None


def resolve(url, timeout=15):
    """展开短链，返回 (最终地址, HTTP 状态码)。只发 HEAD，不下载正文。"""
    r = subprocess.run(
        ['curl', '-sIL', '-m', str(timeout), '-A', UA, '-o', '/dev/null',
         '-w', '%{url_effective} %{http_code}', url], capture_output=True)
    parts = r.stdout.decode('utf-8', 'ignore').strip().rsplit(' ', 1)
    if len(parts) != 2:
        return url, 0
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return parts[0], 0


def github_headers():
    """在 GitHub Actions 里用自带令牌提高限额；本机没有令牌就匿名（搜索接口每分钟 10 次）。"""
    h = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    tok = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if tok:
        h['Authorization'] = f'Bearer {tok}'
    return h
