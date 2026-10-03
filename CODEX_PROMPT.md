# Codex 任务书：把我的影视源打磨到最终形态

## 我（用户）的最终目的 —— 你所有工作都为这几条服务

我在 FongMi 系电视/手机 App 里只填**一个配置地址**，就要得到以下体验，缺一条都不算完成：

1. **打开就是「豆豆片单」**：按电视剧/电影分类浏览，点进任何一部片，换源面板列出**所有**收录这部片的源，点哪个都能播——片单是目录，换源是核心玩法
2. **换源越多越好**：热门片目标 ≥10 个可用源；所有源**免扫码、无弹窗、无引流**；采集站全部走播放链路实测验证，不允许"列表里有、点进去不能用"
3. **全自动保鲜**：站点按实测速度排序（快的在前）；失效站 2 天自动移出、复活自动回归；**我永远不需要手动维护**
4. **直播稳定**：800+ 频道每日筛优，换台快
5. **地址防暴露**：主地址伪装成图片（JPEG 尾部藏配置），多通道备用，任何产物里不出现我的真实身份
6. **源地址长期存活**：上游饭太硬若倒闭/变卦，我的源要能独立运行（快照兜底 + 自有 jar + 自有站库）

## 当前基线（已完成的，别重做）

- 仓库：`WKC0001/source-monitor`（附件 tar 包含全部代码+git 历史+逆向工作区）
- 线上：npm `wkc0001-tvbox@0.1.17`，58 站 = 饭太硬清洗版 34 + 自采采集站 24
- 净化 jar（output/cfg.jpg）：饭太硬 jar 去弹窗重打包版（unidbg 逆向产物，工作区 fty_unpack/）
- 流水线：checker/ 下 6 个脚本，每日 07:00 GitHub Actions 自动跑并发 npm + purge jsDelivr
- 完整机制/坑/发布流程：**先读根目录 HANDOFF.md，那里的 9 条坑全是实测踩出来的，一条都别再踩**

## 你的任务（按此顺序，每项有验收标准）

### 任务 0：环境复现（半小时）
跑通 HANDOFF.md 第四节流水线全绿 → `gh workflow run daily-source-check --repo WKC0001/source-monitor` 发版成功。
**验收**：CI 绿 + npm 出新版本 + cdn.jsdelivr.net 可拉到 58 站。

### 任务 1：源可用性"双验证"（数据面质量，最重要）
现状：探活只测接口延迟，不测"这部片真的搜得到、真的能播"。你要在 checker/site_health.py 或新脚本里加**内容级双验证**：
- **搜索命中**：用 3~5 个真实片名（含全角符号如「年会不能停！2」、纯数字、英文）测每站 `?ac=videolist&wd=`，要求精确命中（vod_name 全等）
- **播放可达**：取命中结果第一集 vod_play_url，实测 m3u8 返回 `#EXTM3U`
- 已知线索：红牛/光速/金鹰/建安/极速/新浪对全角"！2"搜索失效返回无关列表；天堂实测最快 665ms 且可播
- 接入每日 CI：搜索连续 2 天不命中/不可播 → 降备选（复用现有 bench 机制）
**验收**：跑一遍全量报告，产出《58 站可用性矩阵》；劣质站自动降级。

### 任务 2：HideUtils 还原（价值最大，8 个密文站复活）
奶酪/光影/热播/视界/剧圈/咕咕/seed/alllive 的 ext 是饭太硬 native 解密的密文串。注意：**热播/剧圈/咕咕在真机 App 里实测能跑**，说明部分密文运行时可解，别信静态结论。
- 路径 A：用 fty_unpack/ 里现成的 unidbg 环境模拟 ftyguard_v8.so，hook 运行时注入的 dex / HideUtils 调用，dump 解密结果或算法
- 路径 B：社区找已解包的饭太硬变体 jar，直接抽 HideUtils 类合并进净化 jar（smali 合并流程参考 fty_unpack 工作区）
**验收**：至少 3 个密文站在本地能解出明文 ext（dict 配置）且站点结构可被净化 jar 加载。

### 任务 3：源扩容到 70~80 站
走 checker/harvest_extra.py 现有流程（自带成人站黑名单 + host 去重 + 5 秒延迟准入线），候选池：OK影视/俊佬/szyyds 之外再挖 3~5 个社区源。每个新站必须过任务 1 的双验证才准入。
**验收**：总站点 70~80，全部双验证通过，0 成人站，0 扫码站。

### 任务 4：换源体验终验（模拟用户完整路径）
写一个 e2e 脚本：取豆豆片单某分类 → 取 5 部热门片 → 对每部片统计"有多少站精确命中 + 多少站可播" → 产报告。
**验收**：热门片可播源 ≥10；从豆豆片单点进的换源链路（changeable=1）覆盖全部影视站。

### 任务 5：防脆弱性
- 饭太硬挂掉：fty_build 当前 fetch 失败回退快照 fty_base.json——把快照更新做成 CI 每周任务，保证快照永远 ≤7 天新
- jsDelivr 全挂：验证 dc.json 15 通道里 Pages/加速门可用性，挂掉的通道自动剔除
**验收**：模拟上游 404，产物仍能正常构建发布。

## 铁律（违反任何一条 = 工作无效）

1. Python 用 `/Users/ckw/.workbuddy/binaries/python/envs/default/bin/python`；路径全用绝对路径
2. 站点三标记 `searchable/quickSearch/changeable` 必须显式写 1（影视站）；置顶聚合站 changeable 必须 1——**normalize_flags() 别删**
3. 新 csp_ 站点类必须在净化 jar 里真实存在（白名单见 HANDOFF.md 第六节），否则"拼装必死"
4. 发布：api_push.py 的 FILES 列表必须含全部改动文件 → CI 发版 → **逐文件 purge + md5 验证**（@latest 逐文件独立缓存；npm dist-tags 有 1 分钟滞后）
5. 不托管、不留真实身份信息、成人站/网盘扫码站零容忍
6. 改完必须本地全流水线跑绿再推，不许"应该好了"

## 环境速查

```bash
PY=/Users/ckw/.workbuddy/binaries/python/envs/default/bin/python
SM=<解压目录>/source-monitor
# 流水线（顺序执行，命令全文见 HANDOFF.md 第四节）
# 发布：python3 $SM/scripts/api_push.py && gh workflow run daily-source-check --repo WKC0001/source-monitor
```
