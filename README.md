# 可转债抢权配售监控网页：GitHub Actions + GitHub Pages 版

这个版本已经从原来的 **FastAPI 后端服务** 改造成 **纯静态网页 + GitHub Actions 定时生成数据**：

- 不需要服务器；
- 不需要 Docker；
- 不需要 GitHub Actions 以外的定时任务；
- GitHub Pages 负责展示网页；
- GitHub Actions 负责定时运行 Python 抓取数据并生成 JSON/CSV。

## 工作原理

```text
GitHub Actions 定时触发
        ↓
安装 Python 依赖
        ↓
运行 scripts/generate_data.py
        ↓
抓取 AkShare 可转债与 A 股行情数据
        ↓
生成 public/data/bonds_latest.json 和 public/data/bonds_latest.csv
        ↓
部署 public/ 到 GitHub Pages
        ↓
用户打开网页查看抢权配售、安全垫、综合收益
```

## 功能

- 展示近期可抢权配售的可转债；
- 展示正股代码、正股价格、涨跌幅、申购日、股权登记日、配售码；
- 计算买入多少股可以配售 1 手/N 手；
- 计算正股买入金额；
- 估算转债盈利；
- 计算安全垫；
- 支持假设正股回撤后的综合收益测算；
- 支持前端手动输入统一预估上市价；
- 支持导出当前筛选结果 CSV；
- 支持 `manual_overrides.csv` 手动覆盖公告确认后的关键数据。

## 一、部署步骤

### 1. 新建 GitHub 仓库

建议仓库名例如：

```text
cb-rights-pages
```

如果你要使用免费 GitHub Pages，建议使用 public 仓库。

### 2. 上传本项目全部文件

把本项目文件上传到仓库根目录，结构应类似：

```text
cb-rights-pages/
├── .github/workflows/deploy-pages.yml
├── manual_overrides.csv
├── public/
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── data/
├── scripts/
│   └── generate_data.py
├── requirements.txt
└── README.md
```

### 3. 开启 GitHub Pages

进入仓库：

```text
Settings → Pages → Build and deployment → Source → GitHub Actions
```

保存即可。

### 4. 手动运行第一次构建

进入：

```text
Actions → Build data and deploy Pages → Run workflow
```

运行成功后，GitHub 会给出 Pages 地址，例如：

```text
https://你的用户名.github.io/cb-rights-pages/
```

## 二、刷新频率

默认 workflow：

```yaml
schedule:
  - cron: "7 1-8 * * 1-5"
```

含义：工作日 UTC 01:07 到 08:07 每小时执行一次，也就是北京时间/新加坡时间 09:07 到 16:07 每小时刷新一次。

如果想改成每 30 分钟一次，可以修改为：

```yaml
schedule:
  - cron: "7,37 1-8 * * 1-5"
```

如果想改成每 15 分钟一次：

```yaml
schedule:
  - cron: "7,22,37,52 1-8 * * 1-5"
```

注意：GitHub Actions 定时任务不保证准点，适合做低成本监控，不适合做秒级或分钟级交易决策。

## 三、手动覆盖数据

有些字段一定要以发行公告、交易所公告、券商页面为准。你可以编辑根目录：

```text
manual_overrides.csv
```

格式：

```csv
bond_code,record_date,allot_per_share,expected_price,remark
123456,2026-07-10,2.35,122.0,公告已确认
```

字段说明：

- `bond_code`：转债代码，必填；
- `record_date`：股权登记日，格式 `YYYY-MM-DD`；
- `allot_per_share`：每股获配额，单位通常为元/股；
- `expected_price`：你主观预估上市价，例如 122 表示每张 122 元；
- `remark`：备注。

保存并 push 到 GitHub 后，workflow 会重新部署。也可以进入 Actions 页面手动运行一次。

## 四、本地测试

你也可以在本地先测试数据生成：

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
python scripts/generate_data.py
```

然后用任意静态服务器打开：

```bash
cd public
python -m http.server 8000
```

浏览器访问：

```text
http://127.0.0.1:8000
```

## 五、关键计算口径

- 1 手可转债 = 10 张 = 1000 元面值；
- 所需股数 = `ceil(目标手数 × 1000 ÷ 每股获配额 ÷ 100) × 100`；
- 正股买入金额 = `所需股数 × 正股最新价`；
- 转债理论收益 = `目标手数 × 1000 × (预估上市价 - 100) ÷ 100`；
- 安全垫 = `转债理论收益 ÷ 正股买入金额 × 100%`；
- 综合收益 = `转债理论收益 - 正股买入金额 × 假设正股回撤%`。

## 六、和服务器版的区别

| 项目 | 原 FastAPI 版 | GitHub Actions + Pages 版 |
|---|---|---|
| 是否需要服务器 | 需要 | 不需要 |
| 是否有后端 API | 有 | 没有 |
| 刷新方式 | 后端实时刷新/定时刷新 | Actions 定时生成 JSON |
| 网页部署 | 服务器/Docker | GitHub Pages |
| 手动刷新数据 | 调用 `/api/refresh` | 去 Actions 页面 Run workflow |
| 成本 | 取决于服务器 | public 仓库通常可免费使用 |

## 风险提示

本项目只做数据整理和测算，不构成投资建议。抢权配售风险主要来自：正股除权/回撤、申购资金占用、配售规则差异、公告变更、数据接口延迟或错误、上市价格不及预期等。真实操作前必须以交易所公告、发行公告和券商交易页面为准。
