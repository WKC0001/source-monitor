#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集站分类守卫（v2 白名单模式）：拉取所有 type 0/1 CMS 站的 ?ac=list 分类表，
**只保留明确安全的分类**（电影/剧/综艺/动漫等），其余一律视为不安全，生成 category_guard.json。

v1 黑名单模式的教训：成人库分类全是黑话（多人群交/口交自慰/FC2/东京热/一本道/
淫妻绿帽/黑丝诱惑…），关键词黑名单永远堵不完 → 反转为白名单。

缓存结构 {host: {"name": 站名, "adult": [不安全分类], "whitelist": [安全分类], "verdict": ...}}
- verdict=pure_adult（安全分类占比过低）→ build 时整站剔除
- verdict=mixed/clean → build 时注入 site.categories 白名单
- verdict=unknown（拉取失败）→ build 时整站剔除（fail-closed，宁可错杀）

CI 可每日复跑（分类表变了自动跟随）；本地构建只读缓存，不依赖网络。
"""
import json, os, re, ssl, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "category_guard.json")
TIMEOUT = 15

# ★安全分类白名单：分类名必须命中这里才允许出现在首页导航（主闸门）
SAFE_PAT = re.compile(
    r"电影|剧|动漫|综艺|纪录|体育|足球|篮球|音乐|戏曲|娱乐|资讯|新闻|"
    r"八卦|预告|花絮|影评|少儿|亲子|教育|历史|探索|旅游|美食|特摄|邵氏"
)

# ★不安全关键词（副闸门，防止黑话分类蹭安全词混入，如"激情动漫"蹭"动漫"）
ADULT_PAT = re.compile(
    r"伦理|情色|色情|性爱|性交|强奸|乱伦|偷拍|自拍|写真|福利|无码|有码|AV|av|"
    r"三级|巨乳|美乳|人妻|熟女|萝莉|少妇|制服|丝袜|OL|SM|调教|虐待|理论|"
    r"同性|女同|男同|探花|嫖娼|麻豆|传媒|影业|制片|蜜桃|天美|精东|糖心|"
    r"水果派|swag|Swag|SWAG|黑料|网曝|换脸|秀色|大秀|视讯|主播|"
    r"国产精品|国产视频|国产厂商|国产专区|校园春色|极品学生|野战|户外|"
    r"角色扮演|反差|重口|猎奇|童颜|性感|解说|教程|真人秀|美女|热舞|"
    r"抖阴|蘑菇|茄色|嫩模|夜店|欧美精品|采集|"
    r"擦边|两性|直播|抖音|女神|套图|秀人|尤果|尤物|尤蜜|"
    r"爱蜜社|嗲囡囡|波萝社|美媛馆|魅妍|画报|画语|星颜|花漾|星乐园|"
    r"御女郎|影私荟|模范学院|物馆|星馆|顽味|颜|女郎|"
    r"激情|诱惑|人兽|群交|口交|自慰|撸|淫|女优|东京热|一本道|fc2|FC2|"
    r"H漫|h漫|小说|搭讪|偷情|换妻|空姐|技师|会所|素人|门事件|巨臀|翘臀"
)

# 混合库判定阈值：安全分类占比低于此值 → 视为纯成人库整站剔除
PURE_ADULT_RATIO = 0.3

# 站级黑名单：分类名无法用关键词覆盖的成人库（如分类=女优名录），整站判死
HOST_BLACKLIST = {
    "apilj.com": "分类为 JAV 女优名录，成人库",
}


def fetch_classes(api: str):
    """拉分类表：优先 ac=list，失败退回 ac=videolist&pg=1；
    每种拼法重试 2 次，SSL 校验失败自动降级为不校验（部分采集站证书过期）"""
    base = api.split("?", 1)[0]
    ctx_ok = ssl.create_default_context()
    ctx_skip = ssl.create_default_context()
    ctx_skip.check_hostname = False
    ctx_skip.verify_mode = ssl.CERT_NONE
    for q in ("?ac=list", "?ac=videolist&pg=1"):
        for attempt in range(2):
            for ctx in (ctx_ok, ctx_skip):
                try:
                    req = urllib.request.Request(base + q, headers={"User-Agent": "okhttp/3.15"})
                    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                        d = json.loads(r.read().decode("utf-8", "ignore"))
                    cls = d.get("class") or []
                    if cls:
                        return [(int(c.get("type_id", 0)), str(c.get("type_name", "")).strip())
                                for c in cls if str(c.get("type_name", "")).strip()]
                except Exception:
                    continue
    return None


def host_of(api: str) -> str:
    m = re.match(r"https?://([^/?]+)", api)
    return m.group(1) if m else api


def main():
    guard = {}
    if os.path.exists(OUT):
        try:
            guard = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            guard = {}

    sites = []
    for f in ("own_sites.json", "extra_sites.json"):
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                raw = d.get("sites", []) if isinstance(d, dict) else d
                sites += [s for s in raw
                          if s.get("type") in (0, 1) and str(s.get("api", "")).startswith("http")]
            except Exception:
                pass

    print(f"[guard] 待检测 CMS 站: {len(sites)}")
    for s in sites:
        api = str(s["api"]); host = host_of(api.split("?")[0])
        if host in HOST_BLACKLIST:
            guard[host] = {"name": s["name"], "adult": [], "whitelist": [],
                           "verdict": "pure_adult", "reason": HOST_BLACKLIST[host]}
            print(f"[guard] ✂ {s['name']} ({host}): 站级黑名单 — {HOST_BLACKLIST[host]}")
            continue
        if host in guard and guard[host].get("verdict"):
            continue  # 已有完整缓存（含 verdict），跳过；删缓存文件可强刷
        cls = fetch_classes(api)
        if cls is None:
            # fail-closed：拉不到分类表 = 无法证明干净 = 整站剔除
            guard[host] = {"name": s["name"], "adult": [], "whitelist": [],
                           "verdict": "pure_adult", "reason": "分类表拉取失败，fail-closed 剔除"}
            print(f"[guard] ✗ {s['name']} ({host}): 拉取失败 → fail-closed 剔除")
            continue
        # 白名单模式：只保留"命中安全词 且 未命中不安全词"的分类
        white = [n for _, n in cls if SAFE_PAT.search(n) and not ADULT_PAT.search(n)]
        adult = [n for _, n in cls if not (SAFE_PAT.search(n) and not ADULT_PAT.search(n))]
        ratio = len(white) / len(cls) if cls else 0
        verdict = "pure_adult" if ratio < PURE_ADULT_RATIO else ("mixed" if adult else "clean")
        guard[host] = {"name": s["name"], "adult": adult, "whitelist": white, "verdict": verdict}
        tag = {"pure_adult": "★纯成人库(将整站剔除)", "mixed": f"混合库(不安全 {len(adult)}/{len(cls)})",
               "clean": "干净库"}[verdict]
        print(f"[guard] ✓ {s['name']} ({host}): {tag}")
        if white:
            print(f"        安全分类: {'、'.join(white)}")
        if adult and len(adult) <= 12:
            print(f"        剔除分类: {'、'.join(adult)}")

    json.dump(guard, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    pure = [v["name"] for v in guard.values() if v.get("verdict") == "pure_adult"]
    mixed = [v["name"] for v in guard.values() if v.get("verdict") == "mixed"]
    print(f"[guard] 缓存已写: {OUT}")
    print(f"[guard] 纯成人库(将整站剔除): {'、'.join(pure) or '无'}")
    print(f"[guard] 混合库(将注入分类白名单): {'、'.join(mixed) or '无'}")


if __name__ == "__main__":
    main()
