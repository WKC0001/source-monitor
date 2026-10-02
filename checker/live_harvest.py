#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_harvest.py —— 直播源聚合流水线
  下载多个上游社区 m3u/txt → 解析 → 频道名归一去重 → 探活筛选 → 测速排序 → 产出聚合 m3u
产出: output/live.m3u（挂进 api.json lives 外部引用） + output/live_report.json
"""
import json, os, re, sys, time, urllib.request, urllib.error, socket
from concurrent.futures import ThreadPoolExecutor, as_completed

OUT = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else 'output'
UA_TV = "okhttp/4.10.0"
PER_CHANNEL_KEEP = 3      # 每频道最多保留 N 条不同地址（备选线路）
PROBE_TIMEOUT = 6

# 上游清单：社区每日自动更新的聚合源（GitHub 走 jsDelivr gh 通道保证 CI 可达）
UPSTREAMS = [
    {"name": "Guovin-iptv",   "url": "https://cdn.jsdelivr.net/gh/Guovin/iptv@gd/output/result.m3u",        "kind": "m3u"},
    {"name": "vbskycn-iptv4", "url": "https://cdn.jsdelivr.net/gh/vbskycn/iptv@master/tv/iptv4.m3u",        "kind": "m3u"},
    {"name": "suxuang-iptv",  "url": "https://cdn.jsdelivr.net/gh/suxuang/myIPTV@main/ipv4.m3u",            "kind": "m3u"},
    {"name": "joevess-IPTV",  "url": "https://cdn.jsdelivr.net/gh/joevess/IPTV@main/iptv.m3u",              "kind": "m3u"},
    {"name": "iptv-org-cn",   "url": "https://iptv-org.github.io/iptv/countries/cn.m3u",                     "kind": "m3u"},
    {"name": "Kimentanm",     "url": "https://cdn.jsdelivr.net/gh/Kimentanm/aptv@master/m3u/iptv.m3u",       "kind": "m3u"},
    {"name": "YanG-Gather",   "url": "https://cdn.jsdelivr.net/gh/YanG-1989/m3u@main/Gather.m3u",            "kind": "m3u"},
    {"name": "epg-pw",        "url": "https://epg.pw/test_channels_original.m3u",                            "kind": "m3u"},
]

def fetch(url, timeout=20, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA_TV})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', 'ignore')
        except Exception as e:
            last = e
            time.sleep(1)
    raise last

# ---------------- 解析 ----------------
def parse_m3u(text):
    """[(name, group, url, logo)]"""
    out, cur = [], {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXTINF'):
            name = line.rsplit(',', 1)[-1].strip() if ',' in line else ''
            g = re.search(r'group-title="([^"]*)"', line)
            logo = re.search(r'tvg-logo="([^"]*)"', line)
            cur = {"name": name, "group": g.group(1) if g else '', "logo": logo.group(1) if logo else ''}
        elif line.startswith('#'):
            continue
        else:
            if cur.get('name'):
                out.append((cur['name'], cur.get('group', ''), line, cur.get('logo', '')))
            cur = {}
    return out

def parse_txt(text):
    """txt 聚合格式: 分组名,#genre# ... 频道名,url1#url2"""
    out, group = [], ''
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ',#genre#' in line:
            group = line.split(',', 1)[0].strip()
            continue
        if ',' in line:
            name, _, urls = line.partition(',')
            for u in urls.replace('#', '\n').split('\n'):
                u = u.strip()
                if u.startswith(('http', 'rtmp', 'rtp', 'udp')):
                    out.append((name.strip(), group, u, ''))
    return out

# ---------------- 频道名归一 ----------------
def norm_name(raw):
    n = raw.strip()
    n = re.sub(r'\s+', '', n)
    n = re.sub(r'(超清|高清|标清|蓝光|原画|高清4K|4K|8K|50fps|60fps|频道$)', '', n)
    n = n.upper().replace('－', '-').replace('（', '(').replace('）', ')')
    # CCTV 系归一: CCTV-1 / CCTV1综合 -> CCTV1；CCTV5+/CCTV-5+ 保留加号；4K/8K 变体归并
    if n.startswith('CCTV'):
        m = re.match(r'^CCTV-(\d+)(\+)?$', n)
        if m:
            n = f"CCTV{m.group(1)}{m.group(2) or ''}"
        else:
            m = re.match(r'^CCTV(\d+)([+-]?)(\d?[KP])?.*$', n)
            if m:
                n = f"CCTV{m.group(1)}{m.group(2) or ''}"
    n = re.sub(r'^(NEW|SIHUAS|IPTV|CHC|DOXTV|CH_G)[-_]?', '', n)
    return n or raw.strip()

def infer_group(name, orig_group):
    g = (orig_group or '')
    if name.startswith('CCTV') or re.match(r'^CCTV\d+\+?$', name):
        return '央视'
    if '卫视' in name or '卫视' in g:
        return '卫视'
    if any(k in name for k in ('CGTN', 'BBC', 'HBO', 'DISCOVERY', 'NATGEO', 'NHK', 'KBS', 'TVB', '凤凰', '翡翠', '明珠', '国际')):
        return '港澳台国际'
    if any(k in name for k in ('CCTV', '央')) and ' 央' in g:
        return '央视'
    if any(k in g for k in ('央', '央视')):
        return '央视'
    if any(k in g for k in ('体育', 'SPORT')):
        return '体育'
    if any(k in g for k in ('电影', 'MOVIE')):
        return '电影'
    if any(k in g for k in ('记录', '纪实', 'DOC')):
        return '纪录片'
    if any(k in g for k in ('港澳', '香港', '台湾', '海外', '国际')):
        return '港澳台国际'
    if any(k in g for k in ('地方', '省市')):
        return '地方'
    return '其他'

GROUP_ORDER = ['央视', '卫视', '电影', '体育', '纪录片', '港澳台国际', '地方', '其他']

# ---------------- 探活 ----------------
SKIP_PROTO = ('rtp://', 'udp://', 'rtsp://', 'P2p', 'p3p')  # 家宽组播/私有协议在 CI 无法验证，直接剔除

def probe(url):
    """返回 (ok, latency_ms) 或 (False, None)"""
    if url.lower().startswith(SKIP_PROTO):
        return (False, None)
    try:
        t0 = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": UA_TV, "Range": "bytes=0-4095",
        })
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
            code = r.getcode()
            ct = (r.headers.get('Content-Type') or '').lower()
            body = r.read(2048)
        ms = int((time.time() - t0) * 1000)
        if code in (200, 206, 302):
            # 内容必须是疑似媒体流（容错：部分 php 网关返回 application/octet-stream / 无 ct）
            if ct and ('text/html' in ct and b'<html' in body.lower()):
                return (False, None)
            return (True, ms)
        return (False, None)
    except Exception:
        return (False, None)

# ---------------- 主流程 ----------------
def main():
    os.makedirs(OUT, exist_ok=True)
    pool = {}   # norm_name -> list of {name, url, group, logo, src}
    upstream_stats = []

    for up in UPSTREAMS:
        try:
            text = fetch(up['url'])
            entries = parse_m3u(text) if up['kind'] == 'm3u' else parse_txt(text)
        except Exception as e:
            upstream_stats.append({"name": up['name'], "ok": False, "entries": 0, "err": str(e)[:80]})
            print(f"[x] {up['name']}: {str(e)[:60]}")
            continue
        added = 0
        for name, group, url, logo in entries:
            if not url.startswith(('http://', 'https://')):
                continue
            key = norm_name(name)
            if not key:
                continue
            pool.setdefault(key, []).append({
                "raw": name, "url": url, "group": infer_group(key, group),
                "logo": logo, "src": up['name'],
            })
            added += 1
        upstream_stats.append({"name": up['name'], "ok": True, "entries": added})
        print(f"[✓] {up['name']}: {added} 条")

    # 探活（并发 40）
    all_urls = []
    for key, items in pool.items():
        seen_hosts = set()
        for it in items:
            host = re.match(r'https?://([^/]+)', it['url']).group(1)
            if host in seen_hosts:
                continue  # 同频道同 host 只测一次
            seen_hosts.add(host)
            all_urls.append((key, it))
    print(f"频道 {len(pool)} 个，去重后待探测 {len(all_urls)} 条")

    results = {}
    with ThreadPoolExecutor(max_workers=40) as ex:
        futs = {ex.submit(probe, it['url']): (key, it) for key, it in all_urls}
        for i, f in enumerate(as_completed(futs)):
            key, it = futs[f]
            ok, ms = f.result()
            results.setdefault(key, [])
            if ok:
                results[key].append((ms, it))
            if (i + 1) % 200 == 0:
                print(f"  探测进度 {i+1}/{len(all_urls)}")

    # 每频道保留最快 N 条，按分组排序输出
    lines = ['#EXTM3U x-tvg-url="https://ep.112114.xyz/e.xml"', '']
    report = {"upstreams": upstream_stats, "channels": 0, "urls_alive": 0, "groups": {}}
    def gsort(k):
        g = results[k][0][1]['group'] if results.get(k) else '其他'
        return GROUP_ORDER.index(g) if g in GROUP_ORDER else 99
    keys = sorted([k for k in results if results[k]], key=gsort)
    for key in keys:
        best = sorted(results[key], key=lambda x: x[0])[:PER_CHANNEL_KEEP]
        for ms, it in best:
            title = f"#EXTINF:-1 tvg-id=\"{key}.fqzone.tv\" tvg-name=\"{key}\" group-title=\"{it['group']}\",{it['raw']}"
            if it['logo']:
                title = f"#EXTINF:-1 tvg-id=\"{key}.fqzone.tv\" tvg-name=\"{key}\" tvg-logo=\"{it['logo']}\" group-title=\"{it['group']}\",{it['raw']}"
            lines.append(title)
            lines.append(it['url'])
        report['channels'] += 1
        report['urls_alive'] += len(best)
        g = best[0][1]['group']
        report['groups'][g] = report['groups'].get(g, 0) + 1

    m3u_path = os.path.join(OUT, 'live.m3u')
    open(m3u_path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    json.dump(report, open(os.path.join(OUT, 'live_report.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"[✓] {m3u_path}: {report['channels']} 频道 / {report['urls_alive']} 条存活地址 / 分组 {report['groups']}")

if __name__ == '__main__':
    main()
