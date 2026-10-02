#!/usr/bin/env python3
"""分发增强：伪装图 bg.jpg + 多仓 dc.json。纯标准库，CI 零额外依赖。"""
import argparse, base64, json, os, re

SALT = b"qT7xKm4V"          # FongMi Decoder 认 [A-Za-z0-9]{8}\*\* 标记
CARRIER_B64 = "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"          # 100x100 无害几何小图

RAW = "https://raw.githubusercontent.com/WKC0001/source-monitor/main/output/"
GH = "WKC0001/source-monitor"

CHANNELS = [
    ("主·jsDelivr",   f"https://cdn.jsdelivr.net/gh/{GH}@main/output/api.json"),
    ("主·jsDelivr-npm", "https://cdn.jsdelivr.net/npm/wkc0001-tvbox@latest/api.json"),
    ("备·fastly",     f"https://fastly.jsdelivr.net/gh/{GH}@main/output/api.json"),
    ("备·gcore",      f"https://gcore.jsdelivr.net/gh/{GH}@main/output/api.json"),
    ("备·testingcf",  f"https://testingcf.jsdelivr.net/gh/{GH}@main/output/api.json"),
    ("备·quantil",    f"https://quantil.jsdelivr.net/gh/{GH}@main/output/api.json"),
    ("备·Pages",      f"https://wkc0001.github.io/source-monitor/output/api.json"),
    ("备·加速门1",    f"https://ghfast.top/{RAW}api.json"),
    ("备·加速门2",    f"https://ghproxy.net/{RAW}api.json"),
    ("备·加速门3",    f"https://ghproxy.cn/{RAW}api.json"),
    ("伪装·jsDelivr图", f"https://cdn.jsdelivr.net/gh/{GH}@main/output/bg.jpg"),
    # ---- 家源备用通道（源失效时在 App 多仓界面一键切换）----
    ("家源·OK影视",   "https://cdn.jsdelivr.net/gh/cluntop/tvbox@main/box.json"),
    ("家源·俊佬top98", "http://home.jundie.top:81/top98.json"),
    ("家源·szyyds",   "https://szyyds.cn/tv/x.json"),
    ("家源·肥猫",     "http://xn--ihqu10cn4c.xn--z7x900a.love/接口禁止盗用/肥猫.json"),
    # 注1：bmp.ovh 图床按 Accept 头分流（含 text/html 即 302 跳网页），对 App 直连不可控，不进通道池
    # 注2：npmmirror 文件直链是白名单制（cnpm/unpkg-white-list，需 PR 审批且严打分发非库文件的包），未过审前不可用
]


def make_image(api_path: str, out_path: str):
    jpeg = base64.b64decode(CARRIER_B64)
    data = open(api_path, "rb").read()
    b64 = base64.b64encode(data)
    # 校验盐值标记全文件唯一且位于 JPEG 之后（FongMi 取第一个匹配）
    wrapped = jpeg + SALT + b"**" + b64
    m = re.search(rb"[A-Za-z0-9]{8}\*\*", wrapped)
    assert m and m.start() == len(jpeg), "salt mark not unique/positioned"
    open(out_path, "wb").write(wrapped)
    return len(wrapped)


def make_depot(out_path: str):
    depot = {"urls": [{"url": u, "name": n} for n, u in CHANNELS]}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(depot, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="output")
    args = ap.parse_args()
    img = os.path.join(args.out, "bg.jpg")
    depot = os.path.join(args.out, "dc.json")
    size = make_image(os.path.join(args.out, "api.json"), img)
    make_depot(depot)
    print(f"[✓] bg.jpg: {size}B（盐值 {SALT.decode()}** + base64 配置）")
    print(f"[✓] dc.json: {len(CHANNELS)} 条通道")


if __name__ == "__main__":
    main()
