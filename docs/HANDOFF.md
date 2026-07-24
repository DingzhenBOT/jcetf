# 交接提示词（Agent Handoff）

> 用途：把当前 CJETF 项目交给**另一个 agent / 新会话**继续。复制下面「交接提示词」整段给新 agent 即可，它已包含架构、已落地功能、待办与续作路径。详细日志见同目录 `devlog.md`，本文件是它的“速读版 + 续作清单”。

---

## 一、交接提示词（直接复制给新 agent）

```text
你是 CJETF 项目的继续开发 agent。这是一个监控 A股板块资金流向 + 涨跌停，输出 ETF 交易参考意见的网站，≤10 用户，部署在腾讯云 4核/4G/60G（CVM）。请先读以下文件建立上下文，再动手：

1. /workspace/docs/devlog.md —— 完整开发日志（必读，含各 Phase 决策与约束）
2. /workspace/docs/HANDOFF.md —— 本交接文件（速读 + 待办）
3. /workspace/DESIGN.md —— 设计系统规范（9 章节，前端组件配色/排版/间距/阴影的权威来源）
4. /workspace/backend 与 /workspace/frontend —— 代码

【技术栈】
- 后端：Python 3.11 + FastAPI + SQLAlchemy + SQLite(WAL) + Pydantic。`market_quote` 单表按 (symbol_type, symbol, data_kind, timeframe, timestamp) 主键；BAR 含 open/high/low/close/volume/amount/change_percent，SNAPSHOT 含 change_percent。
- 前端：Vue 3.4 + Vite + TS + Tailwind v3.4 + ECharts 5，hash 路由。`BaseChart.vue` 封装 ECharts；A股红涨 #dc2626 / 绿跌 #16a34a。
- 部署：Nginx（Basic Auth + HTTPS，鉴权在 Nginx，后端无鉴权层）反代 FastAPI；worker 进程跑采集与信号评估；systemd 管理 etf-api / etf-worker。

【数据源矩阵（重要：平安证券已彻底弃用）】
- ❌ 平安证券（pa-public-fund-filter / news-search）：用户确认"不能直接拿数据就不用了"，已删除全部依赖。
- ✅ 腾讯自选股 westock-data：`npx -y westock-data-skillhub@1.0.5`，无 key，CVM 可用 → 板块异动（`sector ranking`）。
- ✅ 东财全球资讯 7×24：`np-weblist.eastmoney.com/comm/web/getFastNewsList`，零鉴权 → 实时新闻。
- ✅ 盈米 yingmi：`yingmi-skill-cli mcp call SearchFunds`，需在 CVM 安装并授权 → 场外基金（未装时优雅降级）。
- ✅ a-stock-data（腾讯财经 qt.gtimg.cn 实时行情 / 东财板块排名 / 同花顺热点）：补盘中实时数据窟窿。
- ✅ NeoData金融搜索：自然语言查基金/股票，鉴权缓存 12h。
- ❌ 富途 futuapi：需本机 OpenD 桌面，CVM 无头不可用，仅本地人工分析，不进自动管线。
接入层集中在 backend/app/services/external_data.py（所有函数对失败返回 available:false 字典，绝不抛 500）。

【已落地（可直接用，勿重复造轮子）】
- P6：同花顺式日 K 线（开高低收 + 成交量双 grid + dataZoom 横向缩放 + 红绿）+ ETF 列表综合分/当日涨幅排序。
- P2：场外基金页（/offexchange）+ GET /api/external/offexchange（盈米，未装 CLI 降级）。
- P3：板块异动页（/sectors-movement）+ GET /api/external/sectors/movement（腾讯自选股）。
- P5：首页横向滚动实时资讯条（NewsStrip）+ GET /api/external/news（东财）。
- 测试：backend 205 passed（含 tests/test_api_external.py）；前端 pnpm build 通过。

【待办 / 续作（按优先级）】
1. P1 算法重写（最高优先，核心痛点）：evaluate_etf 只读每日收盘 BAR，从不读实时 SNAPSHOT → 盘中综合分/意见"不变"。需让策略盘中摄入腾讯财经实时报价，重排 intraday 评估到 09:45/10:30/13:30/14:30/14:55（参考 monitoring-alert R1/R2 + ashare-short-term-trading 节点时刻），并铸造新 strategy_version（会重塑历史 Signal，先确认 strategy_hash 口径再灰度）。
2. P4 盘后复盘：用 a-share-daily-review 方法论，收盘后生成复盘摘要写入 Opinion(post_close)。
3. 盈米 CLI 在 CVM 安装+授权：解锁 P2 真实场外基金数据（目前沙箱未装，走降级提示）。
4. 板块异动生产化：westock-data 每次 npx 现场拉包首调慢，建议 CVM 预装或加缓存。
5. 网络波动防御：历史教训——网络抖动曾导致重复命令把代码改坏。改动外部调用务必保留 external_data.py 的 `available:` 降级契约，新增端点沿用 /api/external 的优雅降级风格。

【工作纪律】
- 改动前先 Read 文件再 Edit（本环境 Edit 要求先 Read）。
- 任何外部依赖失败都必须降级而非 500（参考 external_data.py 模式）。
- 每次编码后跑：cd /workspace/backend && python -m pytest -q ；cd /workspace/frontend && pnpm build 。
- 完成任务写到 devlog.md（追加小节，标注日期）后提交；如需同步远程：git push https://<TOKEN>@github.com/DingzhenBOT/jcetf.git HEAD:main （token 见部署环境，勿硬编码进代码）。
- 设计相关改动遵循 /workspace/DESIGN.md。
```

---

## 二、当前状态速览（截至 2026-07-25）

| 项 | 状态 |
|---|---|
| 后端 | FastAPI + SQLite(WAL)，205 测试通过 |
| 前端 | Vue3 + ECharts，pnpm build 通过 |
| 数据源 | 平安已弃用；腾讯自选股 + 盈米 + 东财 + NeoData + a-stock-data |
| 远程仓库 | github.com/DingzhenBOT/jcetf.git，main 已推送至 `101fe16` |
| DESIGN.md | 已入库，随本次推送同步 |

## 三、目录导航

- `backend/app/services/external_data.py` —— 外部 skill 接入层（P2/P3/P5 数据源，**降级契约**所在地）
- `backend/app/api/routers/external.py` —— `/api/external/*` 三个端点
- `frontend/src/views/SectorMovement.vue` / `OffExchange.vue` —— 新页面
- `frontend/src/components/sections/NewsStrip.vue` —— 首页资讯条
- `docs/devlog.md` —— 全量开发日志（C0–C7 章节）
- `DESIGN.md` —— 设计系统规范（9 章节）

## 四、关键约束提醒（踩坑经验）

1. **网络波动曾导致重复命令改坏代码**：外部调用一律降级，不 500。
2. **盈米 CLI 沙箱未装** → 场外基金页当前降级提示，上线前需在 CVM 安装授权。
3. **westock-data 首调慢**（npx 现场拉包）→ 生产建议预装/缓存。
4. **P1 铸造新 strategy_version 会重塑历史 Signal** → 先定口径再灰度，别直接覆盖。
5. **Edit 前必须先 Read**（本 agent 环境硬性要求）。
6. **git token 勿硬编码进源码**，推送时用环境变量/临时 URL。
