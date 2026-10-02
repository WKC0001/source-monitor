#!/usr/bin/env python3
"""Verify native bridge descriptors and preserve existing spider classes/assets."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from restore_native_crypto import NATIVES, PACKAGE


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--jar", type=Path, required=True)
    ap.add_argument("--java-home", type=Path, required=True)
    ap.add_argument("--smali-jar", type=Path, required=True)
    args = ap.parse_args()
    with zipfile.ZipFile(args.baseline) as before, zipfile.ZipFile(args.jar) as after:
        assert before.testzip() is None and after.testzip() is None, "Corrupt jar"
        assert set(before.namelist()) == set(after.namelist()), "Jar resource entries changed"
        for name in before.namelist():
            if name != "classes.dex":
                assert before.read(name) == after.read(name), f"Resource changed: {name}"
    with tempfile.TemporaryDirectory(prefix="verify-crypto-") as tmp:
        root = Path(tmp)
        for label, jar in (("before", args.baseline), ("after", args.jar)):
            subprocess.run([str(args.java_home / "bin/java"), "-cp", str(args.smali_jar),
                            "com.android.tools.smali.baksmali.Main", "d", str(jar),
                            "-o", str(root / label)], check=True)
        # baksmali adds filename suffixes for case collisions on macOS; compare
        # class descriptors rather than nondeterministic filesystem names.
        def by_descriptor(directory):
            result = {}
            for p in directory.rglob("*.smali"):
                content = p.read_text()
                descriptor = next(line.split()[-1] for line in content.splitlines() if line.startswith(".class "))
                assert descriptor not in result, f"Duplicate class: {descriptor}"
                result[descriptor] = content
            return result
        before = by_descriptor(root / "before")
        after = by_descriptor(root / "after")
        expected_new = {f"L{PACKAGE}/{name};" for name in ("HideUtils", "CryptoBridge")}
        assert before.keys() <= after.keys(), "Existing classes removed"
        assert after.keys() - before.keys() <= expected_new, "Unexpected classes added"
        changed = {f"L{PACKAGE}/{name};" for name in ("Init", "DexNative", "HideUtils", "CryptoBridge")}
        for descriptor in before.keys() - changed:
            assert before[descriptor] == after[descriptor], f"Unrelated bytecode changed: {descriptor}"
        spider = root / "after" / PACKAGE
        native = (spider / "DexNative.smali").read_text()
        for signature in NATIVES:
            assert re.search(r"^\.method public static native " + re.escape(signature) + "$", native, re.M), signature
        wrappers = (spider / "HideUtils.smali").read_text()
        for signature in (NATIVES[3], NATIVES[4], NATIVES[5], NATIVES[6], NATIVES[7]):
            assert f"L{PACKAGE}/DexNative;->{signature}" in wrappers, signature
        assert wrappers.count(f"L{PACKAGE}/CryptoBridge;->load()V") == 5
        init = (spider / "Init.smali").read_text()
        assert f"const-class v0, L{PACKAGE}/HideUtils;" in init
        assert init.count("->setClass(") == 1, "Duplicate reflection binding"
        assert init.index("->setClass(") < init.index("->init(Landroid/content/Context;)")
        assert "context()Landroid/app/Application;" in init
        assert '"Guard"' in init and "->substring(II)" in init
        crypto = (spider / "CryptoBridge.smali").read_text()
        assert "Ljava/lang/System;->load(Ljava/lang/String;)V" in crypto
        assert "->getLoader(" not in crypto and "->getSpider(" not in crypto
        print(f"PASS: {len(before)} existing classes preserved; only Init/DexNative changed, two bridge classes added")
        print("PASS: all eight JNI descriptors, five wrappers, reflection binding, assets, and Guard mapping")
        print(f"jar md5: {hashlib.md5(args.jar.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
