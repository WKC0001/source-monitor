#!/usr/bin/env python3
"""饭太硬标准配置组装：以饭太硬整包为基底，替换 spider 为净化 jar + md5。

流程（CI 每日跑）：
  1. 优先拉取饭太硬线上伪装图（他家更新站点我们自动跟进），失败则用仓库快照 fty_base.json
  2. 按 Decoder 盐值规则解码（JPEG EOI 后 [A-Za-z0-9]{8}** + base64）
  3. 宽容解析（支持 // 注释行），替换 spider / wallpaper
  4. 写 output/api.json —— dist_extras.py 再封装成伪装图

净化 jar（output/cfg.jpg）由 fty_unpack 流水线产出，本脚本只算 md5。
"""
import argparse, base64, hashlib, json, os, re, subprocess, sys

SALTS = re.compile(rb"[A-Za-z0-9]{8}\*\*")
FTY_URLS = [
    "http://xn--sss604efuw.top/tv/box111.jpg",   # 饭太硬.top punycode
    "http://饭太硬.top/tv/box111.jpg",
]
JAR_URL = "https://cdn.jsdelivr.net/npm/wkc0001-tvbox@latest/cfg.jpg"
UA = "okhttp/4.10.0"

# ---- 站点清洗：剔除网盘扫码类/B站杂烩类废站（按 api 类名 + 名称 + ext 三重保险）----
PAN_APIS = {
    "csp_MyDriveGuard",   # 我的云盘┃配置（需网盘登录）
    "csp_SeedhubGuard",   # 聚剧┃四盘
    "csp_S_zpsGuard",     # 盘搜/易搜┃四盘
    "csp_YpanSoGuard",    # 盘她┃夸父
    "csp_BpanSoGuard",    # 盘他┃嘟嘟
    "csp_KkSsGuard",      # 抠抠┃搜搜
    "csp_UuSsGuard",      # 优汐┃搜搜
    "csp_WoGGGuard",      # 玩偶哥哥（真机实测弹网盘扫码，纯网盘源）
    "csp_PushGuard",      # 手机┃推送（推送工具，非影视源）
}
DROP_NAME_PAT = re.compile(r"云盘|盘搜|易搜|盘她|盘他|抠抠|优汐|聚剧|哔哔合集|请勿信")
CLOUD_DRIVE_KEY = "Cloud-drive"   # ext 含此 key = 依赖夸克/UC 网盘扫码登录（玩偶哥哥/立播/手机推送）
# 工具/周边类站点：无论多快都排在影视源后面（不抢视频源位置）
TOOL_PAT = re.compile(r"直播|看球|回放|MV|音乐|小说|教学|课堂|启蒙|教育|推送|预告|片单|演唱会")

# ---- 分级排序：直连影视主站（快/不卡）置顶，工具与周边垫底 ----
# 顺序即展示顺序；未命中的站保持原相对顺序排在后面
TIER_A = ["糯米", "文采", "奶酪", "原创", "厂长", "光影", "瓜子", "比特", "热播", "茉莉", "剧圈", "荐片", "奥特"]

# 强制置顶（bucket -1）：片单聚合站作首页，浏览分类后再换源看各家片源
PIN_TOP = ["豆豆"]


def normalize_flags(cfg: dict) -> None:
    """补齐/校正站点标记（换源面板与搜索按这三个字段过滤，缺失时部分 App 变体按不可用处理）：
    - type 0/1 采集站：显式 searchable=1 / quickSearch=1 / changeable=1（缺失会导致换源面板不显示）
    - type 3 影视站（searchable=1）：changeable 强制 1——上游把奶酪/光影/荐片等标了 0，
      用户换源面板里全是 🚫 点不了；放开后换源可用片源数量翻倍
    - 工具站（searchable=0）保持上游原样"""
    fixed_cms = fixed_chg = 0
    for s in cfg.get("sites", []):
        if s.get("type") in (0, 1):
            if s.get("searchable") != 1:
                s["searchable"] = 1
                fixed_cms += 1
            s.setdefault("quickSearch", 1)
            s.setdefault("changeable", 1)
        elif s.get("type") == 3 and s.get("searchable") == 1 and s.get("changeable") != 1:
            s["changeable"] = 1
            fixed_chg += 1
    print(f"[fty] 标记校正: 采集站补齐 {fixed_cms} 个, 影视站放开换源 {fixed_chg} 个")


def clean_sites(cfg: dict) -> None:
    sites = cfg.get("sites", [])
    kept, dropped_cd = [], []
    for s in sites:
        if s.get("api") in PAN_APIS or DROP_NAME_PAT.search(s.get("name", "")):
            continue
        if isinstance(s.get("ext"), dict) and CLOUD_DRIVE_KEY in s["ext"]:
            dropped_cd.append(s.get("name", "?"))  # 网盘扫码依赖站（真机实测弹扫码）
            continue
        kept.append(s)
    dropped = len(sites) - len(kept)
    def rank(s):
        name = s.get("name", "")
        for i, kw in enumerate(TIER_A):
            if kw in name:
                return i
        return len(TIER_A)
    cfg["sites"] = sorted(kept, key=rank)  # sorted 稳定排序，同层保持原序
    print(f"[fty] 站点清洗: {len(sites)} -> {len(kept)}（剔除 {dropped} 个：盘搜/B站杂烩类 + 网盘扫码 {dropped_cd}）")


def fetch_fty_image(dest: str) -> bool:
    for url in FTY_URLS:
        try:
            subprocess.run(
                ["curl", "-sfL", "--max-time", "20", "-A", UA, "-o", dest, url],
                check=True, timeout=30,
            )
            if os.path.getsize(dest) > 3000:
                print(f"[fty] 线上配置获取成功: {url} ({os.path.getsize(dest)}B)")
                return True
        except Exception as e:
            print(f"[fty] {url} 失败: {e}")
    return False


def decode_image(path: str) -> bytes:
    data = open(path, "rb").read()
    eoi = data.rfind(b"\xff\xd9")
    tail = data[eoi + 2:]
    m = SALTS.search(tail)
    if not m:
        raise RuntimeError("盐值标记未找到，图片不含配置")
    return base64.b64decode(tail[m.end():])


def tolerant_parse(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    lines = [l for l in text.splitlines() if not l.strip().startswith("//")]
    return json.loads("\n".join(lines))


def jar_md5(path: str) -> str:
    return hashlib.md5(open(path, "rb").read()).hexdigest()


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
    bucket 0 = 实测 ≤800ms（极快，置顶）
    bucket 1 = 无法本地探测的站（饭太硬自家 csp 站，按 TIER_A 名录排序）
    bucket 2/3 = 实测 0.8-2s / >2s
    同桶内按 TIER_A 名录、再按原相对顺序（sorted 稳定排序）。"""
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
            bucket = -1                   # 片单聚合站强制置顶作首页
        elif TOOL_PAT.search(name):
            bucket = 4                    # 工具/周边类永远垫底
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="output")
    ap.add_argument("--snapshot", default=os.path.join(os.path.dirname(__file__), "fty_base.json"))
    ap.add_argument("--jar", default=None, help="净化 jar 路径（默认 output/cfg.jpg）")
    ap.add_argument("--skip-fetch", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    # 1. 取最新饭太硬配置（线上优先，快照兜底）
    cfg = None
    tmp = os.path.join(args.out, "_fty_fresh.jpg")
    if not args.skip_fetch and fetch_fty_image(tmp):
        try:
            cfg = tolerant_parse(decode_image(tmp))
            print(f"[fty] 线上解码成功: {len(cfg['sites'])} 站")
        except Exception as e:
            print(f"[fty] 线上解码失败，回退快照: {e}")
            cfg = None
    if cfg is None:
        cfg = tolerant_parse(open(args.snapshot, "rb").read())
        print(f"[fty] 使用仓库快照: {len(cfg['sites'])} 站")

    # 2. 站点清洗 + 分级排序（好站置顶）
    clean_sites(cfg)

    # 2.5 合并增量站点（harvest_extra.py 采集验证的兼容 CMS/采集站）
    extras_path = os.path.join(os.path.dirname(__file__), "extra_sites.json")
    extras = json.load(open(extras_path, encoding="utf-8")) if os.path.exists(extras_path) else []
    if extras:
        have = {str(s.get("name")) for s in cfg["sites"]}
        add = [s for s in extras if str(s.get("name")) not in have]
        cfg["sites"] += add
        print(f"[fty] 增量站点: +{len(add)}（extra_sites.json 兼容采集站）")

    # 2.6 健康状态机：备选/淘汰站移出产品；快站置顶（site_health.py 每日探测）
    rank = load_rank(args.out)
    kept = [s for s in cfg["sites"]
            if (rank.get(str(s.get("name"))) or {}).get("pool", "active") == "active"]
    n_out = len(cfg["sites"]) - len(kept)
    cfg["sites"] = health_sort(kept, rank)
    if n_out:
        print(f"[fty] 健康过滤: 移出 {n_out} 个失效站（备选仓库 output/bench_sites.json，复活自动回归）")

    # 2.7 标记校正：换源/搜索三字段补齐 + 放开影视站换源（须在排序后跑，覆盖全量站点）
    normalize_flags(cfg)

    # 3. 替换 spider 为净化 jar（带 md5 缓存段）
    jar = args.jar or os.path.join(args.out, "cfg.jpg")
    if not os.path.exists(jar):
        sys.exit(f"[fty] 净化 jar 不存在: {jar}")
    md5 = jar_md5(jar)
    old_spider = cfg.get("spider", "")
    cfg["spider"] = f"{JAR_URL};md5;{md5}"
    print(f"[fty] spider: {old_spider[:60]}... -> {cfg['spider']}")

    # 3. lives：自有聚合直播（live_harvest.py 每日产出）置顶，其余保留精选外部源
    ours = {
        "name": "聚合直播(每日更新)",
        "type": 0,
        "url": "https://cdn.jsdelivr.net/npm/wkc0001-tvbox@latest/live.m3u",
        "playerType": 2,
    }
    lives = [l for l in cfg.get("lives", []) if "ottiptv" in str(l.get("url", ""))
             or "fanmingming" in str(l.get("url", ""))]
    cfg["lives"] = [ours] + lives
    print(f"[fty] lives: 自有聚合置顶 + {len(lives)} 条外部精选 = {len(cfg['lives'])} 条")

    # 4. wallpaper 保留饭太硬动态壁纸（无引流，纯装饰）
    json.dump(cfg, open(os.path.join(args.out, "api.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[✓] api.json（饭太硬标准）: {len(cfg['sites'])} 站 / lives {len(cfg.get('lives', []))} 条")
    print(f"[✓] jar md5: {md5}")


if __name__ == "__main__":
    main()
