#!/usr/bin/env python3
"""自有源独立构建器：完全不依赖饭太硬，只对视频源和直播源负责。

与旧版 fty_build.py 的区别：
  - 不再拉取饭太硬线上配置（他家改版/挂掉/加广告均不影响我们）
  - 站点库 = checker/own_sites.json（自有基底，可手工增删）+ extra_sites.json（采集验证的兼容站）
  - 配置骨架（hosts/logo/rules）自有，无饭太硬壁纸

流程（CI 每日跑）：
  1. 读 own_sites.json 骨架
  2. 清洗守卫（防手工编辑混入扫码站）+ 合并增量站
  3. 健康状态机过滤（失效站进备选仓库）+ 速度排序
  4. 标记校正（searchable/quickSearch/changeable + http->https）
  5. spider 换自有净化 jar + md5；lives 自有聚合置顶
  6. 写 output/api.json —— dist_extras.py 再封装成伪装图
"""
import argparse, hashlib, json, os, re, shutil, sys
from urllib.parse import unquote

JAR_URL = "https://cdn.jsdelivr.net/npm/wkc0001-tvbox@latest/cfg.jpg"
LIVE_URL = "https://cdn.jsdelivr.net/npm/wkc0001-tvbox@latest/live.m3u"

# ---- drpy 脚本自托管：收编饭太硬 EXT 仓库的 js 到自有 npm（脱离其 GitHub 依赖）----
DRPY_PREFIX = "https://cdn.jsdelivr.net/npm/wkc0001-tvbox@latest/drpy/"
DRPY_MAP = {"虎牙.js": "huya.js", "斗鱼直播.js": "douyu.js", "兔小贝.js": "tuxiaobei.js"}

# ---- 清洗守卫：own_sites.json 手工编辑时防混入扫码/网盘类废站 ----
PAN_APIS = {
    "csp_MyDriveGuard", "csp_SeedhubGuard", "csp_S_zpsGuard", "csp_YpanSoGuard",
    "csp_BpanSoGuard", "csp_KkSsGuard", "csp_UuSsGuard", "csp_WoGGGuard", "csp_PushGuard",
}
DROP_NAME_PAT = re.compile(r"云盘|盘搜|易搜|盘她|盘他|抠抠|优汐|聚剧|哔哔合集|请勿信")
CLOUD_DRIVE_KEY = "Cloud-drive"   # ext 含此 key = 依赖夸克/UC 网盘扫码登录

# 工具/周边类站点：无论多快都排在影视源后面（不抢视频源位置）
TOOL_PAT = re.compile(r"直播|看球|回放|MV|音乐|小说|教学|课堂|启蒙|教育|推送|预告|片单|演唱会")

# 分级排序：直连影视主站（快/不卡）置顶，工具与周边垫底
TIER_A = ["糯米", "文采", "奶酪", "原创", "厂长", "光影", "瓜子", "比特", "热播", "茉莉", "剧圈", "荐片", "奥特"]

# 强制置顶（bucket -1）：片单聚合站作首页，浏览分类后再换源看各家片源
PIN_TOP = ["豆豆"]

# lives 外部精选（非饭太硬依赖，独立公共服务）
EXTERNAL_LIVES = [
    {"name": "虎牙一起看", "type": 0, "url": "https://sub.ottiptv.cc/huyayqk.m3u", "playerType": 2, "timeout": 10, "ua": "okHttp/Mod-1.5.0.0"},
    {"name": "斗鱼一起看", "type": 0, "url": "https://sub.ottiptv.cc/douyuyqk.m3u", "playerType": 2, "timeout": 10, "ua": "okHttp/Mod-1.5.0.0"},
    {"name": "YY轮播", "type": 0, "url": "https://sub.ottiptv.cc/yylunbo.m3u", "playerType": 2, "timeout": 10, "ua": "okHttp/Mod-1.5.0.0"},
]


def clean_sites(cfg: dict) -> None:
    """守卫性清洗：正常情况下 own_sites.json 已是干净的，这里防手工编辑引入废站。"""
    sites = cfg.get("sites", [])
    kept = []
    for s in sites:
        if s.get("api") in PAN_APIS or DROP_NAME_PAT.search(str(s.get("name", ""))):
            continue
        if isinstance(s.get("ext"), dict) and CLOUD_DRIVE_KEY in s["ext"]:
            continue
        kept.append(s)
    dropped = len(sites) - len(kept)
    if dropped:
        print(f"[own] 清洗守卫: 剔除 {dropped} 个扫码/网盘类站")
    cfg["sites"] = kept


def normalize_flags(cfg: dict) -> None:
    """补齐/校正站点标记（换源面板与搜索按这三个字段过滤，缺失时部分 App 变体按不可用处理）：
    - type 0/1 采集站：显式 searchable=1 / quickSearch=1 / changeable=1
    - type 3 影视站（searchable=1）：changeable 强制 1，放开换源
    - 置顶聚合站（豆豆片单）：changeable 强制 1——FongMi 详情页换源入口取决于当前站自身标记
    - type 0/1 http 明文采集站升级 https（域名站，IP 站除外）"""
    fixed_cms = fixed_chg = fixed_http = 0
    for s in cfg.get("sites", []):
        if s.get("type") in (0, 1):
            if s.get("searchable") != 1:
                s["searchable"] = 1
                fixed_cms += 1
            s.setdefault("quickSearch", 1)
            s.setdefault("changeable", 1)
            api = str(s.get("api", ""))
            host = api.split("//", 1)[-1].split("/", 1)[0].split("?", 1)[0]
            if api.startswith("http://") and not re.match(r"^\d+\.\d+\.\d+\.\d+(:\d+)?$", host):
                s["api"] = "https://" + api[len("http://"):]
                fixed_http += 1
        elif s.get("type") == 3 and s.get("searchable") == 1 and s.get("changeable") != 1:
            s["changeable"] = 1
            fixed_chg += 1
        if s.get("type") == 3 and any(kw in str(s.get("name")) for kw in PIN_TOP):
            if s.get("changeable") != 1:
                s["changeable"] = 1
                fixed_chg += 1
    print(f"[own] 标记校正: 采集站补齐 {fixed_cms}, 放开换源 {fixed_chg}, http->https {fixed_http}")


def load_rank(out_dir: str) -> dict:
    """site_health.py 产出的站点健康状态 {name: {pool, latency_ms, ...}}"""
    p = os.path.join(out_dir, "site_rank.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8")).get("sites", {})
        except Exception:
            pass
    return {}


def health_sort(sites: list, rank: dict) -> list:
    """速度优先排序：
    bucket -1 = 片单聚合站（豆豆）强制置顶作首页
    bucket 0  = 实测 ≤800ms（极快）
    bucket 1  = 无法本地探测的站（csp 密文 ext 站，按 TIER_A 名录排序）
    bucket 2/3 = 实测 0.8-2s / >2s
    bucket 4  = 工具/周边类永远垫底"""
    def key(item):
        s, idx = item
        name = str(s.get("name", ""))
        r = rank.get(name) or {}
        lat = r.get("latency_ms")
        tier = len(TIER_A)
        for i, kw in enumerate(TIER_A):
            if kw in name:
                tier = i
                break
        if any(kw in name for kw in PIN_TOP):
            bucket = -1
        elif TOOL_PAT.search(name):
            bucket = 4
        elif lat is None:
            bucket = 1
        elif lat <= 800:
            bucket = 0
        elif lat <= 2000:
            bucket = 2
        else:
            bucket = 3
        return (bucket, tier, lat if lat is not None else 99999, idx)
    return [s for _, s in sorted(enumerate(sites), key=lambda x: key((x[1], x[0])))]


def vendor_drpy(cfg: dict, out_dir: str) -> None:
    """drpy 站（虎牙/斗鱼/儿童启蒙）的 api/ext 原指向饭太硬 GitHub 仓库（fantaiying7/EXT），
    是最后的运行时上游依赖。收编方案：js 已快照到 checker/drpy_vendor（ASCII 文件名），
    构建时复制到 output/drpy/ 随 npm/GitHub 双通道发布，配置里全部改写为自有地址。"""
    src = os.path.join(os.path.dirname(__file__), "drpy_vendor")
    dst = os.path.join(out_dir, "drpy")
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(src):
        if f.endswith(".js"):
            shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
    n = 0
    for s in cfg.get("sites", []):
        for key in ("api", "ext"):
            v = s.get(key)
            if isinstance(v, str) and "fantaiying7/EXT" in v:
                fname = unquote(v.rsplit("/", 1)[-1])
                s[key] = DRPY_PREFIX + DRPY_MAP.get(fname, fname)
                n += 1
    if n:
        print(f"[own] drpy 收编: {n} 处上游地址改写为自有 npm 通道")


def jar_md5(path: str) -> str:
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="output")
    ap.add_argument("--base", default=os.path.join(os.path.dirname(__file__), "own_sites.json"),
                    help="自有站点库骨架（默认 checker/own_sites.json）")
    ap.add_argument("--jar", default=None, help="净化 jar 路径（默认 output/cfg.jpg）")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    # 1. 读自有骨架（无任何上游网络依赖）
    skeleton = json.load(open(args.base, encoding="utf-8"))
    cfg = {
        "spider": "",
        "wallpaper": "",
        "hosts": skeleton.get("hosts", []),
        "logo": skeleton.get("logo"),
        "rules": skeleton.get("rules", []),
        "sites": list(skeleton.get("sites", [])),
        "lives": [],
    }
    print(f"[own] 自有站点库: {len(cfg['sites'])} 站（own_sites.json）")

    # 2. 清洗守卫 + 合并增量站
    clean_sites(cfg)
    extras_path = os.path.join(os.path.dirname(__file__), "extra_sites.json")
    extras = json.load(open(extras_path, encoding="utf-8")) if os.path.exists(extras_path) else []
    if extras:
        have = {str(s.get("name")) for s in cfg["sites"]}
        add = [s for s in extras if str(s.get("name")) not in have]
        cfg["sites"] += add
        print(f"[own] 增量站点: +{len(add)}（extra_sites.json 兼容采集站）")

    # 3. 健康状态机过滤 + 速度排序
    rank = load_rank(args.out)
    kept = [s for s in cfg["sites"]
            if (rank.get(str(s.get("name"))) or {}).get("pool", "active") == "active"]
    n_out = len(cfg["sites"]) - len(kept)
    cfg["sites"] = health_sort(kept, rank)
    if n_out:
        print(f"[own] 健康过滤: 移出 {n_out} 个失效站（备选仓库 output/bench_sites.json，复活自动回归）")

    # 4. 标记校正 + drpy 收编
    normalize_flags(cfg)
    vendor_drpy(cfg, args.out)

    # 5. spider 换自有净化 jar（带 md5 缓存段）
    jar = args.jar or os.path.join(args.out, "cfg.jpg")
    if not os.path.exists(jar):
        sys.exit(f"[own] 净化 jar 不存在: {jar}")
    md5 = jar_md5(jar)
    cfg["spider"] = f"{JAR_URL};md5;{md5}"

    # 6. lives：自有聚合直播置顶 + 外部精选；壁纸键移除（不留饭太硬依赖）
    cfg["lives"] = [{
        "name": "聚合直播(每日更新)",
        "type": 0,
        "url": LIVE_URL,
        "playerType": 2,
    }] + EXTERNAL_LIVES
    cfg.pop("wallpaper", None)

    json.dump(cfg, open(os.path.join(args.out, "api.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[✓] api.json（自有独立版）: {len(cfg['sites'])} 站 / lives {len(cfg['lives'])} 条")
    print(f"[✓] jar md5: {md5}")


if __name__ == "__main__":
    main()
