#!/usr/bin/env python3
"""Restore the bundled FTY native crypto bridge in an existing clean spider jar.

Requires a JDK, apktool 3's bundled smali/baksmali, and Android build-tools d8.
The input jar is preserved; the output is replaced only after assembly succeeds.
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

NATIVES = [
    "proxyInvoke(Ljava/lang/Object;Ljava/lang/Object;)[Ljava/lang/Object;",
    "getSpider(Ljava/lang/Object;Ljava/lang/String;)Ljava/lang/Object;",
    "getLoader(Ljava/lang/Object;)Ljava/lang/Object;",
    "encrypt(Ljava/lang/String;)Ljava/lang/String;",
    "decrypt(Ljava/lang/String;)Ljava/lang/String;",
    "native_ting_md5(Ljava/lang/String;)Ljava/lang/String;",
    "calcResult([I)[I",
    "noxSign(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
]
PACKAGE = "com/github/catvod/spider"


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def class_bodies(directory):
    result = {}
    for p in directory.rglob("*.smali"):
        lines = [line.strip() for line in p.read_text().splitlines() if line.strip()]
        descriptor = next(line.split()[-1] for line in lines if line.startswith(".class "))
        result[descriptor] = lines
    return result


def build(args):
    java = args.java_home / "bin/java"
    javac = args.java_home / "bin/javac"
    bridge = Path(__file__).resolve().parents[1] / "checker/jar_patch"
    with tempfile.TemporaryDirectory(prefix="fty-crypto-") as temp:
        work = Path(temp)
        with zipfile.ZipFile(args.input) as jar:
            members = {n: jar.read(n) for n in jar.namelist()}
        for name in ("classes.dex", "assets/ftyguard_v7.so", "assets/ftyguard_v8.so"):
            if name not in members:
                raise ValueError(f"Required jar entry missing: {name}")
        dex = work / "classes.dex"
        dex.write_bytes(members["classes.dex"])
        smali = work / "smali"
        run(java, "-cp", args.smali_jar, "com.android.tools.smali.baksmali.Main",
            "d", dex, "-o", smali)
        original_bodies = class_bodies(smali)

        native_path = smali / PACKAGE / "DexNative.smali"
        native = native_path.read_text()
        for signature in NATIVES:
            if re.search(r"^\.method .* " + re.escape(signature) + r"$", native, re.M):
                continue
            native += f"\n.method public static native {signature}\n.end method\n"
        native_path.write_text(native)

        init_path = smali / PACKAGE / "Init.smali"
        init = init_path.read_text()
        if ".field private static app:Landroid/app/Application;" not in init:
            raise ValueError("Expected clean Init.app field missing; do not patch unknown jars")
        if "context()Landroid/app/Application;" not in init:
            init += """
.method public static context()Landroid/app/Application;
    .locals 1
    sget-object v0, Lcom/github/catvod/spider/Init;->app:Landroid/app/Application;
    return-object v0
.end method
"""
        binding = """    const-class v0, Lcom/github/catvod/spider/HideUtils;
    invoke-static {v0}, Lcom/github/catvod/spider/InitOrigin;->setClass(Ljava/lang/Class;)V
"""
        marker = "    invoke-static {p0}, Lcom/github/catvod/spider/InitOrigin;->init(Landroid/content/Context;)V"
        if marker not in init:
            raise ValueError("Expected InitOrigin.init call missing")
        bound = re.search(
            r"const-class v0, Lcom/github/catvod/spider/HideUtils;\s+"
            r"invoke-static \{v0\}, Lcom/github/catvod/spider/InitOrigin;->setClass\(Ljava/lang/Class;\)V",
            init,
        )
        if not bound:
            init = init.replace(marker, binding + "\n" + marker, 1)
        init_path.write_text(init)

        # Compile-only stubs. They are never included in the resulting jar.
        stubs = {
            "android/os/Build.java": "package android.os; public class Build { public static String CPU_ABI; }",
            "android/content/Context.java": "package android.content; public class Context { public java.io.File getCacheDir() { return null; } }",
            "android/app/Application.java": "package android.app; public class Application extends android.content.Context {}",
            f"{PACKAGE}/Init.java": "package com.github.catvod.spider; public class Init { public static android.app.Application context() { return null; } }",
            f"{PACKAGE}/DexNative.java": """package com.github.catvod.spider; public class DexNative {
                public static native String encrypt(String v);
                public static native String decrypt(String v);
                public static native String native_ting_md5(String v);
                public static native int[] calcResult(int[] v);
                public static native String noxSign(String a, String b, String c);
            }""",
        }
        stub_paths = []
        for name, source in stubs.items():
            p = work / "stubs" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(source)
            stub_paths.append(p)
        classes = work / "compiled"
        classes.mkdir()
        run(javac, "-source", "8", "-target", "8", "-d", classes,
            *stub_paths, bridge / "CryptoBridge.java", bridge / "HideUtils.java")
        bridge_dex = work / "bridge-dex"
        bridge_dex.mkdir()
        run(java, "-cp", args.d8_jar, "com.android.tools.r8.D8", "--min-api", "21",
            "--output", bridge_dex, classes / PACKAGE / "CryptoBridge.class",
            classes / PACKAGE / "HideUtils.class")
        run(java, "-cp", args.smali_jar, "com.android.tools.smali.baksmali.Main",
            "d", bridge_dex / "classes.dex", "-o", work / "bridge-smali")
        for p in (work / "bridge-smali").rglob("*.smali"):
            target = smali / p.relative_to(work / "bridge-smali")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(p.read_bytes())
        if class_bodies(smali) == original_bodies:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if args.input.resolve() != args.output.resolve():
                shutil.copyfile(args.input, args.output)
            print(f"Native crypto bridge already current: {args.output}")
            return
        run(java, "-cp", args.smali_jar, "com.android.tools.smali.smali.Main",
            "a", smali, "-o", work / "patched.dex")
        members["classes.dex"] = (work / "patched.dex").read_bytes()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        staging = args.output.with_suffix(".tmp")
        with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as jar:
            for name, data in members.items():
                entry = zipfile.ZipInfo(name, (2026, 10, 3, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                jar.writestr(entry, data)
        staging.replace(args.output)
        print(f"Restored native crypto bridge: {args.output}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--java-home", type=Path, required=True)
    ap.add_argument("--smali-jar", type=Path, required=True)
    ap.add_argument("--d8-jar", type=Path, required=True)
    build(ap.parse_args())


if __name__ == "__main__":
    main()
