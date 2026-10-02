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

    # 2. 替换 spider 为净化 jar（带 md5 缓存段）
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
