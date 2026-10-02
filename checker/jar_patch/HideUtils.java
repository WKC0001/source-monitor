package com.github.catvod.spider;

/** Native crypto bridge used by both direct and InitOrigin reflection calls. */
public final class HideUtils {
    private HideUtils() {}
    public static String decrypt(String value) {
        CryptoBridge.load();
        return DexNative.decrypt(value);
    }
    public static String encrypt(String value) {
        CryptoBridge.load();
        return DexNative.encrypt(value);
    }
    public static int[] calcResult(int[] value) {
        CryptoBridge.load();
        return DexNative.calcResult(value);
    }
    public static String tingMd5(String value) {
        CryptoBridge.load();
        return DexNative.native_ting_md5(value);
    }
    public static String noxSign(String a, String b, String c) {
        CryptoBridge.load();
        return DexNative.noxSign(a, b, c);
    }
}
