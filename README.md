# source-monitor · 自建源地址

每天自动检测点播/直播源存活，现役池出问题自动用候补池顶上，产出稳定的 `output/api.json` 作为你自己的源地址。

## 使用

1. 把本仓库推到你的 GitHub
2. 启用 Actions（默认开启），每天北京时间 07:00 自动运行
3. 产出地址（填进 FM影视/TVBox）：
   - jsDelivr：`https://cdn.jsdelivr.net/gh/<你的用户名>/source-monitor@main/output/api.json`
   - GitHub Pages：仓库 Settings → Pages → 选 main 分支根目录，然后 `https://<用户名>.github.io/source-monitor/output/api.json`
4. 日常维护：只编辑 `sources.yaml`（往池里加源），其余全自动

## 双池机制

- `pools.active` 现役池 → 进入 api.json 生效
- `pools.bench` 候补池 → 每日同步检测，现役连续失败 2 天降级，候补连续成功自动晋升顶缺
- 候补连续失败 5 天 → 移入 `output/history/` 归档（attic），不再自动回池

## 检测层级

| 层 | 内容 |
|---|---|
| L1 | 配置可达、合法 JSON（兼容多仓/UTF-8 BOM） |
| L2 | spider jar 可下载 |
| L3 | 每源抽查 6 个 CMS 型站点列表接口，存活率 <34% 判失效 |
| L4 深测（规划） | 抽片实测播放链接 |

## 本地调试

```bash
pip install pyyaml
python checker/check.py
```
