/**
 * df-edge · 配置分发壳
 * 路径即密钥：/<KEY> 才返回配置，其他一律 404
 * 双上游自动切换（jsDelivr -> Pages），边缘缓存 10 分钟
 * 单 IP 每分钟 30 次，超出 429（隔离实例级，够用）
 */
export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const key = url.pathname.replace(/^\/+|\/+$/g, "").replace(/\.json$/i, "");

    // 密钥校验：不带 key 或 key 错 -> 404（伪装成空站）
    if (!key || key !== env.KEY) {
      return new Response("Not Found", { status: 404 });
    }

    // 简单限流：单 IP 每分钟 30 次
    const ip = req.headers.get("cf-connecting-ip") || "unknown";
    const now = Date.now();
    const rl = (globalThis.__rl ||= new Map());
    const e = rl.get(ip) || { n: 0, t: now };
    if (now - e.t > 60000) { e.n = 0; e.t = now; }
    if (++e.n > 30) return new Response("Too Many Requests", { status: 429 });
    rl.set(ip, e);
    if (rl.size > 10000) rl.clear();

    // 边缘缓存
    const cache = caches.default;
    let res = await cache.match(req);
    if (!res) {
      for (const origin of [env.ORIGIN1, env.ORIGIN2]) {
        try {
          const r = await fetch(origin, { redirect: "follow" });
          if (r.ok) { res = r; break; }
        } catch (_) { /* 下一个上游 */ }
      }
      if (!res) return new Response("Upstream Error", { status: 502 });
      res = new Response(res.body, res);
      res.headers.set("Cache-Control", "public, max-age=600");
      ctx.waitUntil(cache.put(req, res.clone()));
    }
    return res;
  },
};
