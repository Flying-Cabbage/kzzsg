# 可转债抢权配售看板 V3

这是 GitHub Actions + GitHub Pages 静态部署版，不需要服务器。

V3 主要增强：

- 首页统计卡片：今日登记、明日登记、未来 3 天、A 级机会、最高安全垫、最低资金门槛；
- 机会看板：今日与临近重点、安全垫排行、未来待发行池；
- 主表升级：等级、阶段、买股金额、预估收益、安全垫、回撤后综合收益、风险等级；
- 条件筛选：未来天数、配售手数、统一预估上市价、正股回撤、最低安全垫、最高买股金额、阶段、排序、关键词；
- 单只转债详情弹窗：基础信息、1/2/5/10 手配售测算、盈亏矩阵、风险提示；
- CSV 导出：导出当前筛选结果。

## 部署

1. 把本项目推送到 GitHub 仓库根目录。
2. 进入 `Settings → Pages → Build and deployment → Source`，选择 `GitHub Actions`。
3. 进入 `Actions → Build data and deploy Pages → Run workflow`，手动运行一次。
4. 运行成功后访问 Pages 地址。

## 数据刷新

工作流在 `.github/workflows/deploy-pages.yml` 中配置。默认按 UTC 时间运行：

```yaml
schedule:
  - cron: "7 1-8 * * 1-5"
```

这相当于北京时间工作日 09:07 到 16:07 每小时刷新一次。

改成 30 分钟一次：

```yaml
schedule:
  - cron: "7,37 1-8 * * 1-5"
```

GitHub Actions 的 cron 使用 UTC，不支持在 `schedule` 中直接写 `timezone`。

## 手动覆盖数据

如果公告数据比接口更准确，可以改 `manual_overrides.csv`。支持字段：

```csv
bond_code,record_date,allot_per_share,expected_price,remark
```

例如：

```csv
123456,2026-07-10,2.3450,125,公告确认配售比例
```

## 计算口径

- 1 手可转债按 10 张、1000 元面值估算；
- 买入股数按 100 股整数倍向上取整；
- 安全垫 = 预计转债收益 ÷ 正股买入金额；
- 综合收益 = 预计转债收益 - 正股买入金额 × 假设正股回撤比例；
- 综合等级是前端模型评分，只用于排序和初筛，不构成投资建议。
