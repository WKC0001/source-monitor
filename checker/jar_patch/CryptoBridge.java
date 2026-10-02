package com.github.catvod.spider;

import android.os.Build;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.security.MessageDigest;

/** Loads only the bundled crypto library; never invokes the native dex loader. */
public final class CryptoBridge {
    private static boolean loaded;
    private CryptoBridge() {}
    public static synchronized void load() {
        if (loaded) return;
        String abi = Build.CPU_ABI;
        if (!abi.startsWith("arm") && !abi.startsWith("armeabi")) {
            throw new IllegalStateException("Unsupported crypto ABI: " + abi);
        }
        String asset = "assets/ftyguard_" + (abi.contains("64") ? "v8" : "v7") + ".so";
        try {
            byte[] data;
            try (InputStream in = CryptoBridge.class.getClassLoader().getResourceAsStream(asset)) {
                if (in == null) throw new IllegalStateException("Missing crypto asset: " + asset);
                java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
                data = out.toByteArray();
            }
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(data);
            StringBuilder hex = new StringBuilder();
            for (byte b : digest) hex.append(String.format("%02x", b & 255));
            File lib = new File(Init.context().getCacheDir(), "ftycrypto-" + hex + ".so");
            if (!lib.isFile() || lib.length() != data.length) {
                if (lib.exists() && !lib.delete()) throw new IllegalStateException("Cannot replace crypto library");
                try (FileOutputStream out = new FileOutputStream(lib)) { out.write(data); }
                if (!lib.setReadOnly()) throw new IllegalStateException("Cannot protect crypto library");
            }
            System.load(lib.getAbsolutePath());
            loaded = true;
        } catch (Exception e) {
            throw new IllegalStateException("Cannot load bundled crypto library", e);
        }
    }
}
