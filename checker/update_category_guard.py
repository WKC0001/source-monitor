#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集站分类守卫：拉取所有 type 0/1 CMS 站的 ?ac=list 分类表，
按关键词识别成人分类，生成 category_guard.json 缓存。

缓存结构 {host: {"name": 站名, "adult": [成人分类], "whitelist": [正常分类]}}
- whitelist 为空 = 纯成人库 → build 时整站剔除
- whitelist 非空 = 混合库 → build 时注入 site.categories 白名单

CI 可每日复跑（分类表变了自动跟随）；本地构建只读缓存，不依赖网络。
"""
import json, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "category_guard.json")
TIMEOUT = 12

# 成人/低俗分类关键词（在分类名上匹配；宁枉勿纵，误杀最多损失一个无害边缘分类）
ADULT_PAT = re.compile(
    r"伦理|情色|色情|性爱|性交|强奸|乱伦|偷拍|自拍|写真|福利|无码|有码|AV|av|"
    r"三级|巨乳|美乳|人妻|熟女|萝莉|少妇|制服|丝袜|OL|SM|调教|虐待|理论|"
    r"同性|女同|男同|探花|嫖娼|麻豆|传媒|影业|制片|蜜桃|天美|精东|糖心|"
    r"水果派|swag|Swag|黑料|网曝|换脸|秀色|大秀|视讯|主播|"
    r"国产精品|国产视频|国产厂商|国产专区|校园春色|极品学生|野战|户外|"
    r"角色扮演|反差|重口|猎奇|童颜|性感|解说|教程|真人秀|美女|热舞|"
    r"抖阴|蘑菇|精东|茄色|嫩模|夜店|欧美精品|采集|"
    r"擦边|两性|直播|抖音|女神|套图|秀人|尤果|尤物|尤蜜|"
    r"爱蜜社|嗲囡囡|波萝社|美媛馆|魅妍|画报|画语|星颜|花漾|星乐园|"
    r"御女郎|影私荟|模范学院|物馆|星馆|顽味|颜|女郎"
)

# 混合库判定阈值：正常分类占比低于此值 → 视为纯成人库整站剔除
PURE_ADULT_RATIO = 0.3

# 站级黑名单：分类名无法用关键词覆盖的成人库（如分类=女优名录），整站判死
HOST_BLACKLIST = {
    "apilj.com": "分类为 JAV 女优名录，成人库",
}


def fetch_classes(api: str):
    """拉分类表：优先 ac=list，失败退回 ac=videolist&pg=1"""
    base = api.split("?", 1)[0]
    sep = "&" if base.endswith("=") or "?ac=" in base else "?"
    # api 可能已带 ?ac=list 尾巴
    url = base if base.endswith("/") else base
    for q in ("?ac=list", "?ac=videolist&pg=1"):
        try:
            req = urllib.request.Request(url + q, headers={"User-Agent": "okhttp/3.15"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
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
            print(f"[guard] ✗ {s['name']} ({host}) 分类拉取失败，保持原缓存" if host in guard
                  else f"[guard] ✗ {s['name']} ({host}) 分类拉取失败，跳过")
            continue
        adult = [n for _, n in cls if ADULT_PAT.search(n)]
        white = [n for _, n in cls if not ADULT_PAT.search(n)]
        ratio = len(white) / len(cls) if cls else 0
        verdict = "pure_adult" if ratio < PURE_ADULT_RATIO else ("mixed" if adult else "clean")
        guard[host] = {"name": s["name"], "adult": adult, "whitelist": white, "verdict": verdict}
        tag = {"pure_adult": "★纯成人库(将整站剔除)", "mixed": f"混合库(成人 {len(adult)}/{len(cls)})",
               "clean": "干净库"}[verdict]
        print(f"[guard] ✓ {s['name']} ({host}): {tag}")
        if white:
            print(f"        正常分类: {'、'.join(white)}")
        if 0 < len(adult):
            print(f"        成人分类: {'、'.join(adult)}")

    json.dump(guard, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    pure = [v["name"] for v in guard.values() if v.get("verdict") == "pure_adult"]
    mixed = [v["name"] for v in guard.values() if v.get("verdict") == "mixed"]
    print(f"[guard] 缓存已写: {OUT}")
    print(f"[guard] 纯成人库(将整站剔除): {'、'.join(pure) or '无'}")
    print(f"[guard] 混合库(将注入分类白名单): {'、'.join(mixed) or '无'}")


if __name__ == "__main__":
    main()
