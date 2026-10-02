#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""站点健康状态机：每日探测产物内全部站点的接口延迟，维护 active/bench/attic 三池。

规则（用户需求映射）：
  - 失效（连续 2 天探活失败）→ 移出 api.json，进「备选仓库」bench（output/bench_sites.json 可见）
  - 备选站每日继续探测，连续 2 天复活 → 自动加回 api.json
  - bench 连续失败 5 天 → attic（继续探测，复活路径同样存在，永不删除）
  - 每次探测记录延迟 → output/site_rank.json，fty_build 用它把快站排前面

探测方式按站点类型：
  - type 0/1 苹果CMS      -> GET api?ac=videolist&pg=1 验列表
  - type 3 csp_AppYsV2    -> GET ext 网址验 json
  - type 3 drpy(.js)      -> GET js 文件验可达
  - type 3 dict ext 含 siteUrl/url -> GET 首页验可达
  - type 3 密文 ext        -> 无法本地探测，标记 skip，永不降级（饭太硬自家站多属此类）

状态存 state.json 的 "site_pools" 键，与 check.py 的源级状态（顶层键）互不干扰。
"""
import argparse, concurrent.futures as cf, json, os, socket, ssl, time
import urllib.request

UA = {"User-Agent": "okhttp/4.9.3"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
TODAY = time.strftime("%Y-%m-%d")

DEMOTE_FAIL = 2   # active 连续失败 N 天 -> bench（移出产品，进备选）
DEAD_FAIL = 5     # bench 连续失败 N 天 -> attic
PROMOTE_OK = 2    # bench 连续成功 N 天 -> active（自动回归产品）


def fetch(url, timeout=10, retries=1, max_bytes=300_000):
    socket.setdefaulttimeout(timeout)
    last = None
    for i in range(retries + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, context=CTX) as r:
                r.read(max_bytes)
                return True, int((time.time() - t0) * 1000)
        except Exception as e:
            last = e
            time.sleep(0.8)
    return False, str(type(last).__name__)


def probe_target(site):
    """返回该站点的探测 URL；None = 不可探测（skip）"""
    api, ext, typ = str(site.get("api", "")), site.get("ext"), site.get("type")
    if typ in (0, 1) and api.startswith("http"):
        sep = "&" if "?" in api else "?"
        return f"{api}{sep}ac=videolist&pg=1", "list"
    if typ == 3:
        if api.endswith(".js") and api.startswith("http"):
            return api, "ping"
        if isinstance(ext, str) and ext.startswith("http"):
            return ext, "json"
        if isinstance(ext, str) and api.startswith("csp_AppYsV2"):
            return None, "skip"          # ext 缺失的 AppYsV2
        if isinstance(ext, dict):
            for k in ("siteUrl", "url", "api"):
                v = ext.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    return v, "ping"
        return None, "skip"              # 密文 ext 等，无法本地验证
    return None, "skip"


def probe_site(item):
    name, url, mode = item
    if not url:
        return name, "skip", 0
    ok, lat = fetch(url)
    if not ok:
        return name, "fail", 0
    return name, "ok", lat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--api", default="output/api.json")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    state = {}
    if os.path.exists(args.state):
        state = json.load(open(args.state, encoding="utf-8"))
    reg = state.setdefault("site_pools", {})

    cfg = json.load(open(args.api, encoding="utf-8"))
    current = cfg.get("sites", [])

    # 1. 注册表更新：现产物站点 + 留存 bench/attic 站点（复活探测）
    todo = {}   # name -> (probe_url, mode)
    for s in current:
        url, mode = probe_target(s)
        name = str(s.get("name", "?"))
        r = reg.setdefault(name, {"pool": "active", "fail_streak": 0, "ok_streak": 0,
                                  "last_ok": None, "site": {"type": s.get("type"), "api": s.get("api"),
                                                            "ext": s.get("ext") if isinstance(s.get("ext"), (str, dict)) else None}})
        if r.get("pool") not in ("bench", "attic"):
            r["site"] = {"type": s.get("type"), "api": s.get("api"),
                         "ext": s.get("ext") if isinstance(s.get("ext"), (str, dict)) else None}
        if mode != "skip" or url:
            todo[name] = (url, mode)
    for name, r in reg.items():          # bench/attic 继续探测以便复活
        if r.get("pool") in ("bench", "attic"):
            st = r.get("site") or {}
            url, mode = probe_target(st)
            if url:
                todo[name] = (url, mode)

    # 2. 并发探测
    items = [(n, u, m) for n, (u, m) in todo.items()]
    with cf.ThreadPoolExecutor(12) as ex:
        results = list(ex.map(probe_site, items))
    by_name = {n: (st, lat) for n, st, lat in results}

    # 3. 状态机推进（同日重复运行只刷新延迟，不重复累计）
    events = []
    for name, r in reg.items():
        st, lat = by_name.get(name, ("absent", 0))
        if st == "absent":
            continue                      # 注册表遗留但产物与 bench 均无（上游已删），不动
        if st == "skip":
            r["probe"] = "skip"
            continue
        if r.get("last_probe") == TODAY:
            if st == "ok":
                r["latency_ms"] = lat
            continue
        r["last_probe"] = TODAY
        if st == "ok":
            r["ok_streak"] = r.get("ok_streak", 0) + 1
            r["fail_streak"] = 0
            r["latency_ms"] = lat
            r["last_ok"] = TODAY
            pool = r["pool"]
            if pool in ("bench", "attic") and r["ok_streak"] >= PROMOTE_OK:
                r["pool"] = "active"
                events.append(f"⬆️ 复活 {name}（连续成功 {r['ok_streak']} 天，自动加回）")
            elif pool == "attic" and r["ok_streak"] == 1:
                r["pool"] = "bench"
                events.append(f"🪜 {name} 出现复活迹象（attic -> bench 观察）")
        else:
            r["fail_streak"] = r.get("fail_streak", 0) + 1
            r["ok_streak"] = 0
            pool = r["pool"]
            if pool == "active" and r["fail_streak"] >= DEMOTE_FAIL:
                r["pool"] = "bench"
                events.append(f"⬇️ 移入备选 {name}（连续失败 {r['fail_streak']} 天）")
            elif pool == "bench" and r["fail_streak"] >= DEAD_FAIL:
                r["pool"] = "attic"
                events.append(f"⚰️ {name} 移入 attic（连续失败 {r['fail_streak']} 天）")

    # 4. 产物：rank 文件（fty_build 排序/过滤用）+ 备选清单（人可读）
    rank = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "sites": {}}
    for name, r in reg.items():
        rank["sites"][name] = {"pool": r.get("pool", "active"),
                               "latency_ms": r.get("latency_ms"),
                               "fail_streak": r.get("fail_streak", 0),
                               "ok_streak": r.get("ok_streak", 0)}
    json.dump(rank, open(os.path.join(args.out, "site_rank.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    bench = [{"name": n, **(r.get("site") or {}), "fail_streak": r.get("fail_streak")}
             for n, r in sorted(reg.items()) if r.get("pool") in ("bench", "attic")]
    json.dump(bench, open(os.path.join(args.out, "bench_sites.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    state["site_pools"] = reg
    json.dump(state, open(args.state, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    n_active = sum(1 for r in reg.values() if r.get("pool") == "active")
    n_bench = sum(1 for r in reg.values() if r.get("pool") == "bench")
    n_attic = sum(1 for r in reg.values() if r.get("pool") == "attic")
    print(f"[health] 探测 {len(items)} 站 | active {n_active} / bench {n_bench} / attic {n_attic}")
    for e in events:
        print("   ", e)
    print(f"[✓] site_rank.json / bench_sites.json -> {args.out}")


if __name__ == "__main__":
    main()
