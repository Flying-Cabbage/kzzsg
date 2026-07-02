# 可转债发行生命周期监控系统

这是一套可以直接部署到 GitHub Actions + GitHub Pages 的 A 股可转债监控系统。

系统按可转债生命周期跟踪：

```text
董事会预案 → 股东大会通过 → 交易所受理 → 问询/回复 → 审核通过 → 证监会注册 → 发行公告 → 股权登记日 → 申购/配债 → 发行结果 → 上市 → 转股期/下修/强赎/回售 → 摘牌
```

## 功能

- 自动抓取巨潮公告标题，识别可转债流程节点。
- 自动抓取可转债发行表，补齐股权登记日、优先申购日、网上申购日、缴款日、转股起止日。
- 自动抓取东方财富可转债基础表，补齐发行规模、上市日期、评级、到期日等。
- 前端静态页面展示：抢权核心池、风险等级、流程状态、最新公告、CSV 下载。
- GitHub Actions 工作日自动更新，也支持手动运行。

## 文件结构

```text
.github/workflows/update.yml     GitHub Actions 定时更新任务
scripts/update_data.py           数据抓取、状态识别、JSON/CSV 输出
docs/index.html                  页面入口
docs/app.js                      前端逻辑
docs/style.css                   页面样式
docs/data/*.json                 自动生成的数据文件
docs/data/convertibles.csv       自动生成的 CSV
requirements.txt                 Python 依赖
```

## 本地运行

```bash
pip install -r requirements.txt
python scripts/update_data.py
```

然后打开：

```text
docs/index.html
```

## 部署到 GitHub Pages

1. 新建 GitHub 仓库。
2. 把本项目全部文件复制进去并提交。
3. 进入仓库 `Settings → Pages`。
4. `Build and deployment` 选择：
   - Source: `Deploy from a branch`
   - Branch: `main`
   - Folder: `/docs`
5. 保存后等待 Pages 生效。
6. 进入 `Actions`，手动运行一次 `update-convertible-bonds`。

## 定时更新说明

`.github/workflows/update.yml` 里默认按工作日执行四次：

```yaml
- cron: '25 0 * * 1-5'   # 北京时间 08:25
- cron: '40 3 * * 1-5'   # 北京时间 11:40
- cron: '10 7 * * 1-5'   # 北京时间 15:10
- cron: '0 14 * * 1-5'   # 北京时间 22:00
```

GitHub Actions 的 cron 使用 UTC 时间，所以这里已经换算成北京时间。

## 状态口径

- `董事会预案`：只是公司想发，属于早期储备。
- `股东大会通过`：内部授权通过，仍不代表能发行。
- `交易所受理 / 问询 / 回复 / 审核通过`：进入审核流程。
- `已注册等待发行`：证监会同意注册，有发行资格，但还不能确定发行日。
- `已确定发行`：发行公告/募集说明书已披露，股权登记日和申购日开始有操作意义。
- `今日股权登记`：抢权配售的最后关键日。
- `今日申购/配债`：原股东配售/网上申购日。
- `发行成功待上市`：发行结果公告后，等待上市。
- `已上市 / 转股期`：进入上市后生命周期。

## 环境变量

可以在 Actions 或本地设置：

```bash
LOOKBACK_DAYS=1200        # 公告回看天数
CNINFO_MAX_PAGES=25       # 巨潮每个关键词最多抓多少页
CNINFO_PAGE_SIZE=30       # 每页条数
REQUEST_TIMEOUT=20        # 请求超时秒数
```

## 注意

- 数据源网页接口可能调整，脚本已经做了容错，某一个数据源失败时页面仍会使用其他数据源。
- 股权登记日、申购日、配售缴款日必须以公司正式公告为准。
- 本系统只做信息整理，不构成投资建议。
