# FTY native crypto bridge

The clean jar contains the decrypted spider dex and `ftyguard_v7.so` / `ftyguard_v8.so`.
The outer shell registers eight JNI methods on `com.github.catvod.spider.DexNative`,
but the decrypted dex's class of that name originally contains only the danmu
loader and native `GoWeb`. Its `HideUtils` references are unresolved, and
`InitOrigin.i` is unset because the shell's reflective bootstrap was removed.

This patch retains the existing `DexNative` implementation, adds the original
eight native declarations, and introduces `HideUtils` wrappers. `Init.init`
binds `HideUtils.class` through `InitOrigin.setClass` before invoking the existing
initializer. Both `Rc`'s direct and reflective crypto paths then resolve.
`CryptoBridge` lazily loads the matching bundled ARM library from the jar's own
class loader. It does not invoke `getLoader`, `getSpider`, or the shell bootstrap.
The existing clean Guard-to-spider mapping is retained.

Build from the repository root using Python and a JDK 17 installation:

```sh
python scripts/restore_native_crypto.py \
  --input output/cfg.jpg --output fty_unpack/cfg-native.jpg \
  --java-home /path/to/jdk17 \
  --smali-jar /path/to/apktool3.jar \
  --d8-jar /path/to/android-build-tools/lib/d8.jar

python scripts/verify_native_crypto.py \
  --baseline output/cfg.jpg --jar fty_unpack/cfg-native.jpg \
  --java-home /path/to/jdk17 --smali-jar /path/to/apktool3.jar
```

The verifier checks all existing classes and unmodified bytecode, bundled assets,
JNI descriptors, wrappers, reflection binding, and Guard mapping. Stub Android
classes used during compilation are excluded from the final dex. Reapplying the
patch to a patched jar produces the same jar. Preserve a baseline before replacing
`output/cfg.jpg`, then rebuild `api.json` and `bg.jpg` so the jar MD5 stays aligned.

Native validation on the handoff snapshot: seven ciphertext ext strings decode
on ARM32 and ARM64; Seedhub's base64 ext passes through unchanged. All sixteen
encrypt/decrypt round trips across the eight inputs pass. The retained
encrypted sites are 光影, 奶酪, 热播, 视界 (display name 茉莉), 剧圈, 咕咕, 一直播.
Seedhub's ext is also accepted by the native oracle, but the site remains excluded
by the existing network-drive policy. Decryption tests do not establish playback
availability; verify home, search, details, and playback in an Android client.
