#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选站点采集器：从社区源里筛出「与净化 jar 兼容」的站点，探活验证后写 extra_sites.json。

兼容性铁律（拼装必死教训）：
  - type 0/1（苹果CMS json/xml）不依赖任何 jar -> 永远可加
  - type 3 且 api=csp_AppYsV2 且 ext 是 http(s) 网址 -> 净化 jar 里有该类，可加
  - 其余 type 3（XBPQ/XPath/自家 jar 等）jar 里没有对应类 -> 一律不收

探活：并发 GET api/ext 接口，type 0/1 验 ac=videolist 出列表，AppYsV2 验 ext 可达+json。
产出 checker/extra_sites.json（人工复核后的增量站点清单，CI 每日继续监控其生死）。
"""
import argparse, concurrent.futures as cf, json, os, re, socket, ssl, time
import urllib.request
from urllib.parse import urljoin

UA = {"User-Agent": "okhttp/4.9.3"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 候选上游（与 sources.yaml active/bench 池同源，走可达通道）
CANDIDATE_CONFIGS = [
    ("OK影视-box",  "https://cdn.jsdelivr.net/gh/cluntop/tvbox@main/box.json"),
    ("OK影视-fun",  "https://cdn.jsdelivr.net/gh/cluntop/tvbox@main/fun.json"),
    ("OK影视-jsm",  "https://cdn.jsdelivr.net/gh/cluntop/tvbox@main/jsm.json"),
    ("俊佬-top98",  "http://home.jundie.top:81/top98.json"),
    ("俊佬-xh",     "http://home.jundie.top:81/xh.json"),
    ("szyyds-x",    "https://szyyds.cn/tv/x.json"),
]
USABLE_APIS = {"csp_AppYsV2"}          # 净化 jar 确认存在的通用爬虫类
DROP_PAT = re.compile(r"云盘|盘搜|易搜|盘|扫码|推广|广告|网盘|夸克|迅雷|哔哔合集")
# 成人站黑名单：名称 + 采集域名双保险（社区源常见，必须拦在门外）
SEX_PAT = re.compile(r"AV|少女|白嫖|香奶|美女|写真|福利|奶子|鸡坤|嘿嘿|湿妹|番号|丝袜|诱惑|国产", re.I)
SEX_HOSTS = ("kxgav.com", "msnii.com", "xrbsp.com", "gdlsp.com", "pgxdy.com",
             "apidanaizi.com", "jkunzyapi.com", "155api.com", "heiapi.cc", "afasu.com", "fhapi9.com")
MAX_EXTRA = 40                          # 增量站点上限（产品总规模控制）
MAX_LATENCY = 5000                      # 探活延迟准入线（ms），超慢站不收


def fetch(url, timeout=12, retries=1):
    socket.setdefaulttimeout(timeout)
    last = None
    for i in range(retries + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, context=CTX) as r:
                return r.read(2_000_000).decode("utf-8-sig", "ignore"), int((time.time() - t0) * 1000)
        except Exception as e:
            last = e
            time.sleep(1)
    return None, str(type(last).__name__)


def abs_url(u, base):
    if not isinstance(u, str) or not u:
        return u
    if not u.startswith(("http://", "https://")):
        u = urljoin(base, u)
    if "raw.githubusercontent.com" in u and not u.startswith("https://ghfast.top/"):
        u = "https://ghfast.top/" + u
    return u


def load_sites(url):
    text, lat = fetch(url)
    if not text:
        return [], lat
    try:
        j = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M).lstrip("\ufeff"), strict=False)
    except Exception:
        return [], "parse"
    # 多仓跟第一个子仓
    if isinstance(j, dict) and "urls" in j:
        subs = [u.get("url") for u in (j.get("urls") or []) if isinstance(u, dict)]
        for su in subs[:2]:
            t, _ = fetch(su, retries=0)
            if t:
                try:
                    j = json.loads(t, strict=False)
                    break
                except Exception:
                    continue
    sites = j.get("sites", []) if isinstance(j, dict) else []
    return sites, lat


def api_host(u):
    m = re.search(r"https?://([^/?:]+)", str(u or ""))
    return (m.group(1).lower() if m else "")


def harvest():
    pool, seen, seen_host = [], set(), set()
    for name, url in CANDIDATE_CONFIGS:
        sites, lat = load_sites(url)
        n0 = len(pool)
        for s in sites:
            if not isinstance(s, dict):
                continue
            nm, api, typ = str(s.get("name", "")), str(s.get("api", "")), s.get("type")
            if DROP_PAT.search(nm) or SEX_PAT.search(nm):
                continue
            if typ in (0, 1) and api.startswith("http"):
                ok = True
            elif typ == 3 and api in USABLE_APIS and isinstance(s.get("ext"), str) and s["ext"].startswith("http"):
                ok = True
            else:
                continue
            host = api_host(api) or api_host(s.get("ext"))
            if host and (any(host == h or host.endswith("." + h) for h in SEX_HOSTS) or host in seen_host):
                continue  # 成人采集域名 / 同上游去重（量子x3类）
            key = nm + "|" + api
            if key in seen:
                continue
            seen.add(key)
            if host:
                seen_host.add(host)
            s["_from"] = name
            pool.append(s)
        print(f"[harvest] {name}: {len(sites)} 站 -> 兼容 {len(pool)-n0} (拉取 {lat}ms)")
    return pool


def probe(site):
    api = site.get("api", "")
    urls = []
    if site.get("type") in (0, 1):
        sep = "&" if "?" in api else "?"
        urls.append(f"{api}{sep}ac=videolist&pg=1")
    else:
        urls.append(site["ext"])
    lat_best, ok = None, False
    for u in urls:
        text, lat = fetch(u, timeout=10, retries=1)
        if text is None:
            continue
        lat_best = lat if lat_best is None else min(lat_best, lat)
        if site.get("type") == 3:
            try:
                json.loads(text, strict=False)
                ok = True
            except Exception:
                ok = text.lstrip().startswith(("{", "["))
        else:
            try:
                j = json.loads(text)
                ok = bool(j.get("list"))
            except Exception:
                ok = "<list" in text
    return site, ok, lat_best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "extra_sites.json"))
    ap.add_argument("--max", type=int, default=MAX_EXTRA)
    args = ap.parse_args()

    pool = harvest()
    print(f"[harvest] 兼容候选共 {len(pool)} 个，开始并发探活...")
    with cf.ThreadPoolExecutor(12) as ex:
        results = list(ex.map(probe, pool))
    alive = [(s, l) for s, ok, l in results if ok and l is not None and l <= MAX_LATENCY]
    alive.sort(key=lambda x: x[1])          # 快的优先入选
    print(f"[harvest] 探活通过 {len(alive)} 个，取最快 {args.max} 个")

    out = []
    for s, lat in alive[: args.max]:
        item = {"name": s["name"], "type": s.get("type", 3), "api": abs_url(s["api"], "")}
        if isinstance(s.get("ext"), str) and s["ext"]:
            item["ext"] = abs_url(s["ext"], "")
        if s.get("searchable") == 0:
            item["searchable"] = 0
        out.append(item)
        print(f"  + {s['name']:<18} 来自 {s['_from']:<10} {lat}ms  {item['api'][:50]}")
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[✓] {args.out}: {len(out)} 站")


if __name__ == "__main__":
    main()
