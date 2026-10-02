#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源健康监控 · 主检测器
用法: python3 check.py [--config sources.yaml] [--state state.json] [--out output]
流程: 拉取双池所有源 -> 分层验活 -> 状态机升降级 -> 生成 api.json / report.md / state.json
"""
import argparse, concurrent.futures as cf, hashlib, json, os, re, socket, sys, time
import urllib.request, urllib.error, ssl
from urllib.parse import urljoin
import yaml

UA = {"User-Agent": "okhttp/4.9.3"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
TODAY = time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 基础工具
def fetch(url, timeout=15, retries=2, max_bytes=4_000_000):
    """带重试的抓取，返回 (text|None, latency_ms)"""
    socket.setdefaulttimeout(timeout)
    last = None
    for i in range(retries + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, context=CTX) as r:
                return r.read(max_bytes).decode("utf-8-sig", "ignore"), int((time.time() - t0) * 1000)
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    return None, str(type(last).__name__)


def md5_prefix(url, timeout=15):
    """下载 spider jar 前部并返回可校验信息；jar 较大时只验可达性"""
    text, err = fetch(url, timeout=timeout, retries=1, max_bytes=65536)
    return text is not None, err


# ---------------------------------------------------------------- 点播检测
def parse_config(text):
    """识别单仓/多仓，返回 (sites, spider, lives_hint)"""
    j = json.loads(text)
    if not isinstance(j, dict):
        raise ValueError("config is not a dict")
    if "urls" in j or "storeHouse" in j:  # 多仓：跟随前 3 个子仓
        subs = [u.get("url", u.get("sourceUrl")) for u in (j.get("urls") or j.get("storeHouse") or []) if isinstance(u, dict)]
        sites, spider = [], j.get("spider")
        for su in subs[:3]:
            t, _ = fetch(su, retries=1)
            if not t:
                continue
            try:
                sj = json.loads(t)
                sites += sj.get("sites", [])
                spider = spider or sj.get("spider")
            except Exception:
                pass
        return sites, spider, j.get("lives")
    return j.get("sites", []), j.get("spider"), j.get("lives")


def probe_site_api(site, timeout):
    """L3：mac cms 型站点抽查列表接口（type 0/1）；type 3 蜘蛛型跳过本地探测"""
    if site.get("type") not in (0, 1):
        return "skip", 0
    api = site.get("api", "")
    if not api.startswith("http"):
        return "skip", 0
    sep = "&" if "?" in api else "?"
    text, err = fetch(f"{api}{sep}ac=videolist&pg=1", timeout=timeout, retries=1, max_bytes=300000)
    if not text:
        return "fail", 0
    if site.get("type") == 1 or text.lstrip().startswith("<"):
        return ("ok", 1) if "<list" in text or "<video" in text else ("fail", 0)
    try:
        j = json.loads(text)
        return ("ok", len(j.get("list", []))) if j.get("list") else ("fail", 0)
    except Exception:
        return "fail", 0


def absolutize_spider(spider, base_url):
    """相对路径 spider 解析为绝对地址（否则产物里指向自家域名 404）；
    raw.githubusercontent.com 直连在国内不稳，统一包一层 ghfast 加速门。"""
    if not isinstance(spider, str) or not spider:
        return spider
    if not spider.startswith(("http://", "https://")):
        spider = urljoin(base_url, spider)
    if "raw.githubusercontent.com" in spider and not spider.startswith("https://ghfast.top/"):
        spider = "https://ghfast.top/" + spider
    return spider


def probe_vod(src, policy):
    name, url = src["name"], src["url"]
    out = {"name": name, "url": url, "kind": "vod", "day": TODAY}
    text, lat = fetch(url, timeout=policy["probe_timeout"])
    out["latency_ms"] = lat
    if text is None:
        out.update(ok=False, error=str(lat), site_ok=0, site_total=0)
        return out
    try:
        sites, spider, _ = parse_config(text)
    except Exception as e:
        out.update(ok=False, error=f"parse:{type(e).__name__}", site_ok=0, site_total=0)
        return out
    # 相对路径 spider 解析为绝对地址（如 ./jar/xs.jar），否则产物里指向自家域名 404
    spider = absolutize_spider(spider, url)
    # 命中黑名单关键词的站点直接剔除
    bl = policy.get("source_blacklist_keywords", [])
    sites = [s for s in sites if not any(k in str(s.get("name", "")) for k in bl)]
    out["site_total"] = len(sites)
    # spider jar 可达性
    jar_ok = True
    if isinstance(spider, str) and spider.startswith("http"):
        jar_url = spider.split(";")[0]
        jar_ok, _ = md5_prefix(jar_url, policy["probe_timeout"])
    # 抽查站点接口
    sample = [s for s in sites if s.get("type") in (0, 1)][: policy["probe_sites_per_source"]]
    with cf.ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda s: probe_site_api(s, policy["probe_timeout"]), sample))
    checked = [r for r in results if r[0] != "skip"]
    site_ok = sum(1 for r in checked if r[0] == "ok")
    total_items = sum(r[1] for r in checked)
    if not checked:  # 全是蜘蛛型源，只能以 jar 可达 + 站点数判活
        ok = jar_ok and len(sites) > 3
        out.update(ok=ok, site_ok=None, error=None if ok else "jar_unreachable_or_empty")
    else:
        ratio = site_ok / len(checked)
        ok = jar_ok and len(sites) > 3 and ratio >= 0.34
        out.update(ok=ok, site_ok=f"{site_ok}/{len(checked)}", probe_items=total_items,
                   error=None if ok else f"alive_ratio={ratio:.2f}")
    return out


# ---------------------------------------------------------------- 直播检测
def parse_live(text):
    """兼容 m3u / txt(#genre#) 两种格式 -> {group: {channel: [urls]}}"""
    groups = {}
    if "#EXTM3U" in text or "#EXTINF" in text:
        cur_grp, cur_name = "其他", None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#EXTINF"):
                m = re.search(r'group-title="([^"]*)"', line)
                cur_grp = m.group(1).strip() if m else "其他"
                cur_name = line.split(",")[-1].strip()
            elif line and not line.startswith("#") and cur_name:
                groups.setdefault(cur_grp, {}).setdefault(cur_name, []).append(line)
                cur_name = None
    else:
        grp = "其他"
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",", 1)
            if len(parts) != 2:
                continue
            k, v = parts[0].strip(), parts[1].strip()
            if k.endswith("#genre#") or v.endswith("#genre#"):
                grp = (k if k.endswith("#genre#") else v).replace(",#genre#", "").replace("#genre#", "").strip()
            elif v.startswith(("http", "rtp", "rtmp", "rtsp")):
                groups.setdefault(grp, {}).setdefault(k, []).append(v)
    return groups


def probe_live(src, policy):
    name, url = src["name"], src["url"]
    out = {"name": name, "url": url, "kind": "live", "day": TODAY}
    text, lat = fetch(url, timeout=policy["probe_timeout"])
    out["latency_ms"] = lat
    if text is None:
        out.update(ok=False, error=str(lat), channels=0)
        return out
    groups = parse_live(text)
    n_ch = sum(len(v) for v in groups.values())
    out.update(ok=n_ch >= 10, channels=n_ch, groups=len(groups), error=None if n_ch >= 10 else "too_few_channels")
    return out


# ---------------------------------------------------------------- 状态机
def load_state(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def update_state(state, results, policy):
    """更新 fail_streak/ok_streak 并执行升降级。返回 (state, 事件列表)"""
    events = []
    for r in results:
        st = state.setdefault(r["name"], {"pool": r["_pool"], "kind": r["kind"],
                                          "fail_streak": 0, "ok_streak": 0, "last_ok": None})
        st["pool"] = r["_pool"]
        st["kind"] = r["kind"]
        # 同日重复运行：只刷新明细，不累计连续次数（保证一天多次触发不失真）
        if st.get("last_probe") == TODAY:
            st["latency_ms"] = r.get("latency_ms")
            if r["ok"]:
                st["last_ok"] = st.get("last_ok") or r["day"]
                st["last_detail"] = {k: r[k] for k in ("site_total", "site_ok", "channels", "error") if k in r}
            else:
                st["last_detail"] = r.get("error", "unknown")
            continue
        st["last_probe"] = r["day"]
        st["latency_ms"] = r.get("latency_ms")
        if r["ok"]:
            st["ok_streak"] += 1
            st["fail_streak"] = 0
            st["last_ok"] = r["day"]
            st["last_detail"] = {k: r[k] for k in ("site_total", "site_ok", "channels", "error") if k in r}
        else:
            st["fail_streak"] += 1
            st["ok_streak"] = 0
            st["last_detail"] = r.get("error", "unknown")
    # 降级：现役连续失败
    for name, st in state.items():
        if st["pool"] == "active" and st["fail_streak"] >= policy["demote_fail"]:
            st["pool"] = "bench"
            events.append(f"⬇️ 降级 {name}（连续失败 {st['fail_streak']} 天）")
        elif st["pool"] == "bench" and st["fail_streak"] >= policy["dead_fail"]:
            st["pool"] = "attic"
            events.append(f"⚰️ 移出 {name}（连续失败 {st['fail_streak']} 天，进入 attic）")
    # 晋升：现役有空缺时，从候补按 ok_streak+低延迟提拔
    for kind, target in (("vod", policy["target_active_vod"]), ("live", policy["target_active_live"])):
        active_n = sum(1 for st in state.values() if st["pool"] == "active" and st["kind"] == kind)
        if active_n >= target:
            continue
        cands = [(st["ok_streak"], -(st.get("latency_ms") or 99999), n)
                 for n, st in state.items() if st["pool"] == "bench" and st["kind"] == kind
                 and st["ok_streak"] >= 1]
        cands.sort(reverse=True)
        for _, _, n in cands[: target - active_n]:
            state[n]["pool"] = "active"
            events.append(f"⬆️ 晋升 {n}（候补连续成功 {state[n]['ok_streak']} 天）")
    return state, events


# ---------------------------------------------------------------- 产物渲染
def merge_vod_sites(state, results_by_name, agg):
    """按现役源合并站点：去重 -> 质量分排序 -> 截断。返回 (sites, spider, used_sources)"""
    merged, seen, spider = [], set(), None
    active_vod = [(n, r) for n, r in results_by_name.items()
                  if r["kind"] == "vod" and state.get(n, {}).get("pool") == "active" and r.get("ok")]
    for n, r in active_vod:
        for s in r.get("_sites", []):
            key = s.get("key") or re.sub(r"\s", "", str(s.get("name", "")))
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(s)
            spider = spider or r.get("_spider")
    merged.sort(key=lambda s: 0 if s.get("type") in (0, 1) else 1)  # 稳定型 CMS 排前
    return merged[: agg["max_sites"]], spider, [n for n, _ in active_vod]


def merge_live(state, results_by_name, agg):
    """合并现役直播源：同频道保留前 N 条线路，每组限流"""
    out = {}
    for n, r in results_by_name.items():
        if r["kind"] != "live" or state.get(n, {}).get("pool") != "active" or not r.get("ok"):
            continue
        for grp, chans in (r.get("_groups") or {}).items():
            tgt = out.setdefault(grp, {})
            for ch, urls in chans.items():
                tgt.setdefault(ch, [])
                for u in urls:
                    if len(tgt[ch]) < agg["live_lines_per_channel"] and u not in tgt[ch]:
                        tgt[ch].append(u)
    groups = []
    for grp in sorted(out):
        chans = [{"name": ch, "urls": urls} for ch, urls in sorted(out[grp].items())][: agg["live_max_per_group"]]
        groups.append({"group": grp, "channels": chans})
    return groups


def render_live_entries(state, results_by_name, cfg, epg):
    """饭太硬式 lives：引用外部 m3u 清单（轻量、随上游自动更新），不内嵌频道。
    现役存活的直播源各出一条引用；raw.githubusercontent 包装 ghfast 门；
    再补充公共优质清单。"""
    entries = []
    for n, r in results_by_name.items():
        if r["kind"] != "live" or state.get(n, {}).get("pool") != "active" or not r.get("ok"):
            continue
        url = r["url"]
        if "raw.githubusercontent.com" in url and not url.startswith("https://ghfast.top/"):
            url = "https://ghfast.top/" + url
        e = {"name": n, "type": 0, "url": url, "playerType": 2}
        if epg:
            e["epg"] = f"{epg}?ch={{name}}&date={{date}}"
        entries.append(e)
    for extra in cfg.get("extra_lives", []) or []:
        e = {"name": extra["name"], "type": 0, "url": extra["url"], "playerType": 2}
        if extra.get("ua"):
            e["ua"] = extra["ua"]
        if epg:
            e["epg"] = f"{epg}?ch={{name}}&date={{date}}"
        entries.append(e)
    return entries


def render_api(state, results_by_name, cfg):
    agg = cfg.get("aggregation", {})
    brand = cfg.get("brand", {}) or {}
    epg = cfg.get("epg", "")
    sites, spider, used_vod = merge_vod_sites(state, results_by_name, agg)
    api = {"spider": spider, "sites": sites}
    lives = render_live_entries(state, results_by_name, cfg, epg)
    if lives:
        api["lives"] = lives
    if brand.get("notice"):
        api["notice"] = brand["notice"]        # 打开 App 时底部短暂显示
    if brand.get("wallpaper"):
        api["wallpaper"] = brand["wallpaper"]  # 加载配置自动换壁纸
    return api, used_vod


def render_failover(cfg):
    """全源失效时的兜底配置：FongMi 对 msg 字段直接抛出并在界面显示"""
    msg = (cfg.get("brand", {}) or {}).get("failover_msg")
    return {"msg": msg} if msg else {"msg": "所有源已失效，请联系维护者获取新地址"}


def render_report(state, events, used_vod):
    lines = [f"# 源健康报告 · {TODAY}", "",
             f"现役点播源：{[n for n, s in state.items() if s['pool'] == 'active' and s['kind'] == 'vod']}",
             f"本次启用站点合成来源：{used_vod}", ""]
    if events:
        lines += ["## 本轮变动", ""] + [f"- {e}" for e in events] + [""]
    lines += ["| 池 | 源 | 类型 | 连续失败 | 连续成功 | 最近成功 | 备注 |",
              "|---|---|---|---|---|---|---|"]
    for n, st in sorted(state.items(), key=lambda kv: (kv[1]["pool"], kv[0])):
        lines.append(f"| {st['pool']} | {n} | {st['kind']} | {st['fail_streak']} | "
                     f"{st['ok_streak']} | {st.get('last_ok') or '从未'} | {st.get('last_detail', '')} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sources.yaml")
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    policy = cfg["policy"]
    state = load_state(args.state)

    # 组装待检测清单：active 全测；bench 全测；state 里已有但 yaml 已删除的忽略
    todo = [(p, s) for p in ("active", "bench") for s in cfg["pools"].get(p, [])]
    print(f"[i] 待检测源 {len(todo)} 个")

    def run(item):
        p, s = item
        try:
            r = probe_vod(s, policy) if s["kind"] == "vod" else probe_live(s, policy)
        except Exception as e:
            r = {"name": s["name"], "url": s["url"], "kind": s["kind"], "day": TODAY,
                 "ok": False, "error": f"probe:{type(e).__name__}", "latency_ms": None}
        r["_pool"] = p
        return r

    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(run, todo))

    results_by_name = {}
    for r in results:
        results_by_name[r["name"]] = r

    # 检测结果附带到 state（合并站点用）
    state, events = update_state(state, results, policy)

    # 把解析出的 sites/groups 挂到结果上供渲染（只对现役且存活的做）
    for r in results:
        st = state.get(r["name"], {})
        if r["kind"] == "vod" and r["ok"] and st.get("pool") == "active":
            try:
                text, _ = fetch(r["url"], timeout=policy["probe_timeout"])
                sites, spider, _ = parse_config(text)
                # 此处曾漏掉 urljoin，把 probe_vod 里修好的 spider 又覆盖回相对路径
                spider = absolutize_spider(spider, r["url"])
                r["_sites"], r["_spider"] = sites, spider
            except Exception:
                pass
        if r["kind"] == "live" and r["ok"] and st.get("pool") == "active":
            r["_groups"] = parse_live(fetch(r["url"], timeout=policy["probe_timeout"])[0] or "")

    api, used_vod = render_api(state, results_by_name, cfg)
    # 全源失效兜底：点播与直播全空时改发 msg 弹窗配置，引导用户去公众号拿新地址
    if not api["sites"] and not api.get("lives"):
        api = render_failover(cfg)
        print("[!] 现役源全部失效，api.json 已切换为 failover 弹窗模式")
    report = render_report(state, events, used_vod)

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(os.path.join(args.out, "history", TODAY), exist_ok=True)
    with open(os.path.join(args.out, "api.json"), "w", encoding="utf-8") as f:
        json.dump(api, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(args.out, "history", TODAY, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)

    print(f"[✓] api.json: {len(api.get('sites', []))} 站点, lives 组数 {len(api.get('lives', []))}"
          + (f", notice 已下发" if api.get("notice") else ""))
    for e in events:
        print("   ", e)
    print(f"[✓] report -> {args.out}/report.md")


if __name__ == "__main__":
    main()
