# FM影视源项目 · 交接文档（配合 CODEX_PROMPT.md 使用）

> 交接时间：2026-10-03 ｜ 当前线上版本：npm `wkc0001-tvbox@0.1.17` ｜ 任务书见 CODEX_PROMPT.md，本文是背景知识库

## 一、项目使命

以「饭太硬」配置为模板，维护一套**自有影视源**：复用其站点结构、剔除引流/弹窗/网盘扫码废站、补入自有采集站、每日 CI 自动探活排序、经 npm + jsDelivr 分发。用户在 FongMi 系 App（电视/手机盒子）里填配置地址观看。

**红线**：不托管、化名运营、GitHub 源头（仓库 `WKC0001/source-monitor`，注意不要在任何公开文件里暴露真实身份信息）。

## 二、仓库结构

```
source-monitor/
├── checker/                  # 核心流水线（Python，全部可独立运行）
│   ├── build_final.py        # 主构建（自有独立版）：own_sites.json 基底→清洗守卫→合并增量→分类守卫→健康过滤→排序→标记校正→drpy收编→换spider→产 api.json。不依赖饭太硬
│   ├── update_category_guard.py  # 分类守卫缓存生成器：拉全部 CMS 站 ?ac=list 分类表→关键词识别成人分类→写 category_guard.json（CI 每日刷新，失败降级用缓存）
│   ├── category_guard.json   # 分类守卫缓存 {host: {adult[], whitelist[], verdict: pure_adult/mixed/clean}}
│   ├── own_sites.json        # 自有站点库骨架（34 站基底 + hosts/logo/rules），可手工增删站
│   ├── drpy_vendor/          # 自托管的 drpy 脚本（huya/douyu/tuxiaobei/drpy2.min.js，原饭太硬 EXT 仓库收编）
│   ├── check.py              # 上游源池探活（bench池升降级，state.json 顶层键）
│   ├── live_harvest.py       # 直播源聚合（8上游→归一→并发探活→live.m3u，837+频道）
│   ├── site_health.py        # 站点健康状态机（active/bench/attic，产 site_rank.json）
│   ├── harvest_extra.py      # 采集站采集器（从 OK影视/szyyds 等拉候选→过滤→探活→extra_sites.json）
│   ├── dist_extras.py        # 包装产物（bg.jpg 伪装图 + dc.json 15通道多仓）
│   └── extra_sites.json      # 已验证的 24 个增量采集站（name/type/api/ext）
├── output/                   # 产物目录（api.json / cfg.jpg(jar) / bg.jpg / dc.json / live.m3u / site_rank.json / bench_sites.json）
├── fty_unpack/               # jar 逆向工作区（gitignored）：unidbg dumper、明文类清单
├── scripts/api_push.py       # GitHub API 直推（本地 git 落后远端时用这个，FILES 列表控制推哪些文件）
├── sources.yaml              # check.py 的上游源池配置
├── state.json                # check.py 源池状态（顶层键） + site_pools 注册表（站点状态机）
├── .github/workflows/daily.yml  # 每日 07:00 CI：全流水线 + npm publish + purge jsDelivr
└── edge/ npmstage/           # 边缘分发/npm 暂存
```

## 三、运行环境（本机）

- Python：**必须用** `/Users/ckw/.workbuddy/binaries/python/envs/default/bin/python`（venv，含 requests 等）
- `gh` 已登录（能直接 `gh workflow run` / `gh api`）；npm publish 在 CI 里做（NPM_TOKEN 在仓库 secrets），本地不发版
- **坑：沙箱/终端会剥离 `cd` 前缀**——一律用绝对路径或 `git -C`
- 系统另有一个 `node`（v25），别动

## 四、每日流水线（本地演练顺序 = CI 顺序）

```bash
PY=/Users/ckw/.workbuddy/binaries/python/envs/default/bin/python
SM=<项目目录>
$PY $SM/checker/check.py        --config $SM/sources.yaml --state $SM/state.json --out $SM/output
$PY $SM/checker/live_harvest.py --out $SM/output
$PY $SM/checker/update_category_guard.py  # 刷新分类守卫缓存（网络失败可跳过，用旧缓存）
$PY $SM/checker/build_final.py  --out $SM/output          # 自有独立构建（含分类守卫）
$PY $SM/checker/site_health.py  --state $SM/state.json --api $SM/output/api.json --out $SM/output
$PY $SM/checker/dist_extras.py  --out $SM/output          # 伪装图 + dc.json（必须在 build_final 后跑，包进最新配置）
```

**站点 key 铁律（搜索/换源失效根因）**：FongMi/TVBox 系 App 以站点 `key` 为数据库主键（Room @PrimaryKey），换源时用 key.equals() 识别当前站。**配置里任何站缺 key 都会退化为空串并与其他无 key 站互相冲突**——表现为：采集站在换源面板全部消失、搜索结果归并成一组。build_final.py 的 ensure_keys() 已做兜底（cms_+域名生成唯一 key），但**新增站必须带唯一 key**；排查此类问题先查 key。

**分类守卫（成人内容过滤）**：`pure_adult` 库整站剔除；`mixed` 库注入 site.`categories` 白名单——首页分类导航只显示正常分类，伦理/写真/三级等成人分类不再出现，正常片源照常播放。纯成人判定：正常分类占比 <30% 或站级黑名单（HOST_BLACKLIST，处理分类名=女优名录的关键词法盲区）。**已知限制**：白名单只管分类导航，站内搜索仍是全库（CMS 接口无服务端过滤，客户端无法拦截搜索结果）。

**发布**：`python3 $SM/scripts/api_push.py`（注意 FILES 列表要含所有改过的文件！漏推过 checker/check.py 导致 CI 挂了两轮）→ `gh workflow run daily-source-check --repo WKC0001/source-monitor` → `gh run watch`。

## 五、当前状态（0.1.17）

- **58 站** = 饭太硬清洗后 34 + 增量采集 24；第 1 站豆豆┃片单（PIN_TOP 强制置顶作首页）
- 直播 4 条（自有聚合 m3u 置顶）
- 已剔除：网盘扫码站（ext 含 Cloud-drive 的 玩偶哥哥/立播/手机推送 + 7 个盘搜类）+ 哔哔合集
- 排序规则：bucket -1 片单置顶 → 0 实测≤800ms → 1 不可探测密文站(TIER_A 序) → 2/3 慢站 → 4 工具站垫底

## 六、关键机制与坑（血泪史，务必读完）

1. **FongMi 站点三标记必须显式写**：`searchable/quickSearch/changeable` 缺失时部分 App fork 按不可用处理（换源面板不显示该站）。`fty_build.normalize_flags()` 已统一补 1，别删。
2. **详情页换源入口取决于"当前站"自身的 changeable**（FongMi `VideoActivity.isSiteChangeable()`）。豆豆片单上游标 0，已强制改 1。PIN_TOP 里的站都要保证 changeable=1。
3. **App 本地 DB 会持久化用户在换源面板的手动开关**（存 2），刷新配置不覆盖（`Site.sync()` 只在配置值≠0 时同步）→ 用户端排查怪象第一步永远是**清 App 数据重导配置**。
4. **jsDelivr `@latest` 逐文件独立缓存**：发版后 api.json/bg.jpg/cfg.jpg 必须逐文件 purge+验证；npm dist-tags 有约 1 分钟滞后，别急着判发布失败。
5. **净化 jar**（output/cfg.jpg，1.1MB 壳）：饭太硬 jar 去弹窗版（unidbg 逆向重打包，详见 fty_unpack/）。csp 站点类必须是 jar 里真实存在的类，否则"拼装必死"（打开报初始化失败）。可用类白名单主力 `csp_AppYsV2`，其余：T4/App99/AppSx/AppTT/Appgz/Auete/Nmyswv/Jpys/YCyz/NewCz/Bttwoo/Dm84/SixV/Bili/DouDou/Doubao/Kanqiu/LiveGz/Hmys/YGP/Music/Tingshu275/FirstAid/Alllive/Pan/WebDAV/Local/Youtube/Anime1。禁用（扫码类）：WoGG/Libvio/MyDrive/KkSs/UuSs/S_zps/Seedhub/BpanSo/YpanSo/Push。
6. **采集站准入**：type 0/1 直连最安全；成人站黑名单（名称正则 AV|少女|白嫖|香奶|奶子|鸡坤|嘿嘿|湿妹|番号|丝袜|诱惑|国产 + 域名 kxgav/msnii/xrbsp/gdlsp/pgxdy/apidanaizi/jkunzyapi/155api/heiapi/afasu/fhapi9）+ 5 秒延迟准入线在 harvest_extra.py；同上游 host 去重。
7. check.py 遍历 state.json 顶层时必须跳过 `site_pools` 注册表（非源条目）——已有 isinstance 守卫，改代码时保持。
8. 健康状态机：active 连续失败 2 天 → bench（移出产品进 bench_sites.json）；bench 再败 5 天 → attic；复活连续成功 2 天自动回归。饭太硬密文 ext 站 skip 永不降级。
9. 报错看 exit code 要绕过管道（`| tail` 会吞非零）；后台任务记得 run_in_background。

## 七、遗留任务

见 **CODEX_PROMPT.md** 的任务清单（任务 0~5，按优先级）。补充情报：

- 8 个密文 ext 站（奶酪/光影/热播/视界/剧圈/咕咕/seed/alllive）：热播/剧圈/咕咕 真机能跑，说明部分密文运行时可解，别信静态结论
- 全角符号搜索失效站：红牛/光速/金鹰/建安/极速/新浪（搜「年会不能停！2」返回 20 条无关结果）
- `影视 | 建安` 是 http://154.219.117.232:9981（IP 站无法升 https），健康状态机会自动裁决
- 《年会不能停！2》端到端实测：天堂(665ms)/360/非凡/暴風/量子/艾旦 精确命中且 m3u8 可播

## 八、分发通道（App 里填的地址）

- 主：`https://cdn.jsdelivr.net/npm/wkc0001-tvbox@latest/api.json`
- 备：gh 通道五域名（cdn/fastly/gcore/testingcf/quantil.jsdelivr.net）/ `wkc0001.github.io/source-monitor/output/api.json` / ghfast.top 加速门
- 多仓：`.../npm/wkc0001-tvbox@latest/dc.json`（15 通道）
- 伪装图：`.../npm/wkc0001-tvbox@latest/bg.jpg`（JPEG 尾部盐值 `qT7xKm4V**` + base64 配置，解码逻辑在 fty_build.decode_image）

## 九、给 Codex 的第一步

读 CODEX_PROMPT.md（任务书）→ 跑任务 0 环境复现 → 按 HANDOFF.md 第四节命令操作。两份文档冲突时以 CODEX_PROMPT.md 为准。
