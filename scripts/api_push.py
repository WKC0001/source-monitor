#!/usr/bin/env python3
"""通过 GitHub API 直推 source-monitor（git 协议经沙箱代理不稳时使用）。"""
import argparse, base64, json, subprocess, sys
from pathlib import Path

REPO = "WKC0001/source-monitor"
SM = Path(__file__).resolve().parents[1]

FILES = [
    ".github/workflows/daily.yml",
    "checker/fty_build.py", "checker/dist_extras.py", "checker/harvest_extra.py",
    "checker/check.py", "checker/site_health.py", "checker/extra_sites.json", "checker/live_harvest.py",
    "sources.yaml", "state.json",
    "output/api.json", "output/bg.jpg", "output/dc.json", "output/cfg.jpg",
    "output/live.m3u", "output/live_report.json", "output/site_rank.json",
    "output/bench_sites.json",
    "scripts/api_push.py", "scripts/restore_native_crypto.py", "scripts/verify_native_crypto.py",
    "checker/jar_patch/HideUtils.java", "checker/jar_patch/CryptoBridge.java",
    "checker/jar_patch/README.md",
]
MSG = ("feat: 站点健康状态机 + 增量源扩容 + 速度排序\n\n"
       "- 剔除网盘扫码站：玩偶哥哥/立播(Cloud-drive ext)/手机推送\n"
       "- checker/site_health.py: 每日探测延迟，连续2天失效移入备选(bench)，复活2天自动回归\n"
       "- checker/harvest_extra.py + extra_sites.json: 采集验证16个兼容CMS/采集站(成人站黑名单+同上游去重)\n"
       "- fty_build: 健康过滤 + 速度分桶排序(≤800ms置顶，工具站永远垫底)\n"
       "- daily.yml: 新增 site_health 步骤")


def gh(method, path, data=None):
    cmd = ["gh", "api", "-X", method, f"repos/{REPO}/{path}"]
    if data is not None:
        cmd += ["--input", "-"]
        r = subprocess.run(cmd, input=json.dumps(data).encode(),
                           capture_output=True)
    else:
        r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"gh {method} {path} 失败: {r.stderr.decode()[:500]}")
    return json.loads(r.stdout or b"{}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--files", nargs="+", default=FILES, help="本次发布的仓库相对路径")
    ap.add_argument("--message", default=MSG, help="提交说明")
    ap.add_argument("--dry-run", action="store_true", help="校验发布文件，不写 GitHub")
    args = ap.parse_args()
    files = list(dict.fromkeys(args.files))
    for f in files:
        path = (SM / f).resolve()
        if not path.is_relative_to(SM) or not path.is_file() or f == ".env" or ".git" in Path(f).parts:
            sys.exit(f"无效发布文件: {f}")
    if args.dry_run:
        print("\n".join(files))
        return
    ref = gh("GET", "branches/main")
    parent, base_tree = ref["commit"]["sha"], ref["commit"]["commit"]["tree"]["sha"]
    print(f"[i] remote main = {parent[:10]}")

    tree = []
    for f in files:
        blob = gh("POST", "git/blobs",
                  {"content": base64.b64encode(open(f"{SM}/{f}", "rb").read()).decode(),
                   "encoding": "base64"})
        tree.append({"path": f, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  blob {f}")
    new_tree = gh("POST", "git/trees", {"base_tree": base_tree, "tree": tree})
    commit = gh("POST", "git/commits",
                {"message": args.message, "tree": new_tree["sha"], "parents": [parent],
                 "author": {"name": "WKC0001", "email": "WKC0001@users.noreply.github.com"},
                 "committer": {"name": "WKC0001", "email": "WKC0001@users.noreply.github.com"}})
    gh("PATCH", "git/refs/heads/main", {"sha": commit["sha"], "force": False})
    print(f"[✓] pushed {commit['sha'][:10]}")


if __name__ == "__main__":
    main()
