# -*- coding: utf-8 -*-
"""公共工具：路径、读写 JSON、链接规范化。"""
import datetime
import json
import os
import re
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def p(*parts):
    return os.path.join(ROOT, *parts)


def load(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write('\n')
    os.replace(tmp, path)


def config():
    return load(p('config.json'))


def today():
    """运行日期。可用环境变量 MONITOR_DATE 指定，便于回放历史数据。"""
    return os.environ.get('MONITOR_DATE') or datetime.date.today().isoformat()


def days_between(a, b):
    """b - a 的天数，参数是 YYYY-MM-DD 开头的字符串。"""
    da = datetime.date.fromisoformat(a[:10])
    db = datetime.date.fromisoformat(b[:10])
    return (db - da).days


# ---------- 链接规范化 ----------
# 这些域名下一个路径才对应一款游戏，去重键要带路径；其余域名整站算一款游戏。
# 值是"保留几段路径"。
PATH_HOSTS = {
    'claude.ai': 2,            # /artifact/<id>
    'claude.site': 2,          # /artifacts/<id>
    'github.com': 2,           # /<owner>/<repo>
    'spawn.co': 2,             # /@user/<game>
    'verse8.io': 1,
    'astrocade.com': 3,        # /games/<slug>/<id>
    'rosebud.ai': 2,           # /play/<slug>
    'wavedash.com': 2,         # /games/<slug>
    'levels.io': 1,
    'play.google.com': 9, 'apps.apple.com': 9,
    'store.steampowered.com': 2,
    'youtube.com': 9, 'youtu.be': 1,
    'x.com': 3, 'twitter.com': 3,
    'websim.ai': 2, 'websim.com': 2,
    'lovable.app': 0, 'replit.app': 0,
    'poki.com': 3, 'crazygames.com': 2,
    'funwithcomputervision.com': 1,
    'huggingface.co': 3,
    'reddit.com': 6,
    'news.ycombinator.com': 9,
    'discourse.threejs.org': 3,
    'jamvault.com': 3, 'vibej.am': 3,
    'gamejolt.com': 3, 'newgrounds.com': 3,
    'codepen.io': 3, 'glitch.com': 2,
    'g.co': 2, 'gemini.google.com': 2,
    'chatgpt.com': 2, 'canva.site': 0,
    'remix.gg': 2, 'y8.com': 2,
}
# 这些后缀下，子域名才是项目（a.github.io/b 再带一段路径）。
SUBDOMAIN_SUFFIX = {
    'github.io': 1,    # user.github.io/<repo>
    'itch.io': 1,      # user.itch.io/<game>
    'vercel.app': 0, 'pages.dev': 0, 'netlify.app': 0, 'web.app': 0,
    'fly.dev': 0, 'onrender.com': 0, 'workers.dev': 0, 'surge.sh': 0,
    'replit.app': 0, 'lovable.app': 0, 'glitch.me': 0, 'firebaseapp.com': 0,
    'up.railway.app': 0, 'herokuapp.com': 0, 'bolt.host': 0,
}
TRACK_PARAMS = re.compile(r'^(utm_|ref$|ref_|fbclid|gclid|s$|t$|si$|source$|via$)')


def split(url):
    u = urlsplit(url if '//' in url else 'https://' + url)
    host = (u.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    segs = [s for s in u.path.split('/') if s]
    return host, segs, u.query


def game_key(url):
    """把链接归一成去重键。同一款游戏的不同写法（http/https、www、结尾斜杠、追踪参数）得到同一个键。"""
    if not url:
        return ''
    host, segs, query = split(url)
    if not host:
        return ''
    keep = None
    if host in PATH_HOSTS:
        keep = PATH_HOSTS[host]
    else:
        for suf, n in SUBDOMAIN_SUFFIX.items():
            if host.endswith('.' + suf):
                keep = n
                break
    if keep is None:
        return host                       # 独立域名：整站一款游戏
    key = host
    if keep:
        key += '/' + '/'.join(s for s in segs[:keep])
    if host in ('youtube.com',) and 'v=' in query:
        m = re.search(r'(?:^|&)v=([\w-]+)', query)
        if m:
            key = f'youtube.com/watch/{m.group(1)}'
    return key.rstrip('/').lower() if host != 'claude.ai' else key.rstrip('/')


def root_domain(host):
    """粗略取主域（末两段；常见双后缀取三段）。只用于"子域名和主域是否同一家"的比对。"""
    parts = host.split('.')
    if len(parts) <= 2:
        return host
    if parts[-2] in ('co', 'com', 'org', 'net', 'gov', 'edu') and len(parts[-1]) == 2:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])


def host_of(url):
    return split(url)[0]


def slug(text, maxlen=40):
    s = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return s[:maxlen].strip('-') or 'game'


def fmt_num(n):
    """页面上的数字写法：1 万以上用"万"，保留一位小数；其余用千分位。"""
    if n is None:
        return ''
    n = int(n)
    if n >= 10000:
        v = n / 10000
        s = f'{v:.1f}'.rstrip('0').rstrip('.')
        return f'{s} 万'
    return f'{n:,}'
