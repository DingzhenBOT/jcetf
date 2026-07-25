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
- ✅ a-stock-data 腾讯财经 `qt.gtimg.cn` 实时行情：已接入 `collect_market` 作盘中 ETF/指数 SNAPSHOT 的**附加可靠源**（不封 IP，CVM 首选），让 P1 盘中动量修正真正生效；`backend/app/data_provider/gtimg_client.py` + `collector.collect_realtime_gtimg`。
- ✅ NeoData金融搜索：自然语言查基金/股票，鉴权缓存 12h。
- ❌ 富途 futuapi：需本机 OpenD 桌面，CVM 无头不可用，仅本地人工分析，不进自动管线。
接入层集中在 backend/app/services/external_data.py（所有函数对失败返回 available:false 字典，绝不抛 500）。

【已落地（可直接用，勿重复造轮子）】
- P6：同花顺式日 K 线（开高低收 + 成交量双 grid + dataZoom 横向缩放 + 红绿）+ ETF 列表综合分/当日涨幅排序。
- P2：场外基金页（/offexchange）+ GET /api/external/offexchange（盈米，未装 CLI 降级）。
- P3：板块异动页（/sectors-movement）+ GET /api/external/sectors/movement（腾讯自选股）。
- P5：首页横向滚动实时资讯条（NewsStrip）+ GET /api/external/news（东财）。
- 测试：backend 224 passed（含 tests/test_api_external.py、tests/test_collector_gtimg.py、tests/test_data_quality.py、tests/test_pipeline_idempotency.py）；前端 pnpm build 通过。

【待办 / 续作（按优先级）】
1. ~~P1 算法重写（核心痛点，已落地 2026-07-25）~~：已把 ETF 实时 SNAPSHOT.change_percent 作为「盘中动量加性修正」纳入综合分（engine.py `intraday_momentum_adjustment`），仅当日实时路径生效，铸造新 strategy_version(v2.2)；全量 211 passed。~~**Task A（SNAPSHOT 切腾讯财经 qt.gtimg.cn，已落地 2026-07-25）**~~：gtimg 已注入 `collect_market` 作盘中实时快照附加源，`get_latest_snapshot_change_map` 跨源取 max(timestamp) 命中 gtimg → P1 现在 CVM 真正随实时行情更新。可选增强：参考 ashare-short-term-trading 把盘中评估重排到 09:45/10:30/13:30/14:30/14:55。
2. P4 盘后复盘：用 a-share-daily-review 方法论，收盘后生成复盘摘要写入 Opinion(post_close)。
3. 盈米 CLI 在 CVM 安装+授权：README §3.5 已文档化完整流程（init status/setup --phone/--verify-code/doctor）；交互式手机号+短信验证码需用户本人在 CVM 执行，agent 无法代填。完成后解锁 P2 真实场外基金数据。
4. 板块异动生产化：westock-data 每次 npx 现场拉包首调慢，建议 CVM 预装或加缓存。
5. #67 已修 512000 类 OHLC 脏数据：checker._check_ohlc_consistency + 读路径过滤 ANOMALY + 清理脚本 scripts/flag_ohlc_anomalies.py。已入库坏数据需在 CVM 跑一次 `python -m scripts.flag_ohlc_anomalies --apply` 改标（幂等）。
5. 网络波动防御：历史教训——网络抖动曾导致重复命令把代码改坏。改动外部调用务必保留 external_data.py 的 `available:` 降级契约，新增端点沿用 /api/external 的优雅降级风格。

【工作纪律】
- 改动前先 Read 文件再 Edit（本环境 Edit 要求先 Read）。
- 任何外部依赖失败都必须降级而非 500（参考 external_data.py 模式）。
- 每次编码后跑：cd /workspace/backend && ./venv/bin/python -m pytest -q （venv 在 backend/venv，Python 3.11）；cd /workspace/frontend && pnpm build （前端需 Node ≥18，pnpm 务必用 9.x；pnpm@latest 要求 Node 22.13，Node 20 跑不了）。
- 完成任务写到 devlog.md（追加小节，标注日期）后提交；如需同步远程：git push https://<TOKEN>@github.com/DingzhenBOT/jcetf.git HEAD:main （token 见部署环境，勿硬编码进代码）。
- 设计相关改动遵循 /workspace/DESIGN.md。
```

---

## 二、当前状态速览（截至 2026-07-25）

| 项 | 状态 |
|---|---|
| 后端 | FastAPI + SQLite(WAL)，227 测试通过 |
| 前端 | Vue3 + ECharts，pnpm build 通过 |
| 数据源 | 平安已弃用；腾讯自选股 + 盈米 + 东财 + NeoData + a-stock-data |
| 远程仓库 | github.com/DingzhenBOT/jcetf.git，main 已推送至 `a34d91a`（C13 代码落地） |
| DESIGN.md | 已入库，随本次推送同步 |

## 三、目录导航

- `backend/app/services/external_data.py` —— 外部 skill 接入层（P2/P3/P5 数据源，**降级契约**所在地）
- `backend/app/api/routers/external.py` —— `/api/external/*` 三个端点
- `backend/app/data_provider/gtimg_client.py` —— 腾讯财经 qt.gtimg.cn 实时行情客户端（盘中 SNAPSHOT 附加源）
- `backend/app/collector/collector.py` —— `collect_realtime_gtimg`（collect_market 末尾触发，优雅降级）
- `frontend/src/views/SectorMovement.vue` / `OffExchange.vue` —— 新页面
- `frontend/src/components/sections/NewsStrip.vue` —— 首页资讯条（跑马灯 + 点击弹窗 + 规则影响分析）
- `frontend/src/components/charts/PendulumChart.vue` —— 指数当日涨跌幅摆锤图（首页指数卡）
- `frontend/src/components/charts/GaugeChart.vue` —— 信号综合分 0–100 半圆仪表（ETF 详情页）
- `frontend/src/components/ui/Modal.vue` —— 通用模态框（资讯弹窗复用）
- `frontend/src/lib/newsImpact.ts` —— 规则模板式资讯影响分析（离线，关键词→板块/情绪）
- `backend/app/api/routers/market.py` / `backend/app/repository/signal_repo.py` —— bug②/bug⑥ 后端修复
- `backend/app/data_quality/checker.py` —— `_check_ohlc_consistency`（#67 OHLC 异常检测：非正/high<low/跨度>阈值→ANOMALY）
- `backend/app/data_provider/akshare_adapter.py` —— `_filter_kwargs`（版本漂移容错：按签名过滤 kwargs）；`get_sector_history`(period='日k')；`get_sector_fund_flow_history`(BK→东财板块名解析 + 全量历史按区间裁剪)；`_bk_to_em_fund_flow_name`/`_em_board_name_maps`
- `backend/app/collector/collector.py` —— `_is_on_exchange(m)`（排除场外联接基金，走盈米/开放式基金源）；`backfill_history` / `collect_intraday_minute` 仅采场内 ETF
- `backend/app/collector/collector.py` —— `_collect_bar` / `collect_intraday_minute` 采集后调 `assess`；`collect_realtime_gtimg`（盘中附加源）
- `backend/app/repository/quote_repo.py` —— `get_bar_history` / `get_max_bar_timestamp` / `get_latest_quote` 过滤 ANOMALY
- `backend/app/db/session.py` —— `_ensure_columns` 幂等 ALTER（etf_mapping.listing、signal.phase 存量回填）
- `backend/app/evaluation/pipeline.py` —— `post_collection_evaluate` 写 Signal.phase
- `backend/scripts/flag_ohlc_anomalies.py` —— 已入库 OHLC 脏数据改标 ANOMALY（dry-run / --apply / --symbol）
- `frontend/src/components/IndexDrawer.vue` —— 大盘抽屉「当日分时 / 日K线」Tab（默认分时）
- `frontend/src/views/EtfDetail.vue` —— 结论 Hero 盘中优先 + 阶段/时间标注；意见拆「盘中意见」/「收盘后复盘」
- `frontend/src/lib/tier.ts` —— `phaseText` / `isIntradayPhase`
- `docs/devlog.md` —— 全量开发日志（C0–C12 章节）
- `DESIGN.md` —— 设计系统规范（9 章节）

## 四、关键约束提醒（踩坑经验）

1. **网络波动曾导致重复命令改坏代码**：外部调用一律降级，不 500。
2. **盈米 CLI 沙箱未装** → 场外基金页当前降级提示；README §3.5 已文档化安装授权流程，需在 CVM 由用户本人完成手机号+短信交互授权。
3. **westock-data 首调慢**（npx 现场拉包）→ 生产建议预装/缓存。
4. **P1 铸造新 strategy_version 会重塑历史 Signal** → 先定口径再灰度，别直接覆盖。
5. **Edit 前必须先 Read**（本 agent 环境硬性要求）。
6. **git token 勿硬编码进源码**，推送时用环境变量/临时 URL。
