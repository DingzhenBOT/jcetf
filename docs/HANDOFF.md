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

【数据源矩阵（重要：平安证券已彻底弃用；东财 em 已于 C14 弃用）】
- ❌ 平安证券（pa-public-fund-filter / news-search）：用户确认"不能直接拿数据就不用了"，已删除全部依赖。
- ❌ 东财(em)：腾讯云直连被 RST 拦截 + 新版 akshare 签名漂移（C13）。**C14 起退出采集轮转**（`DataSourceConfig.preferred="sina"`、`fallback=["ths","tx"]`），适配器内 em source map 保留但 dormant；ETF/指数历史改走新浪(sina)。板块趋势/资金流：新版 `get_sector_history` 在 ordered sources 内**只构造 ths**（em 不进轮转，因 CVM 上 em 被 RST 拦截）；`_BK_TO_THS` 覆盖 6/8 跟踪板块（军工/新能源车/5G/证券/银行/白酒），2 个（医药 BK0465、消费 BK0438）THS 无单一聚合板、设计内不可映射 → D4 降级（sector_score/fund_flow_score=None，引擎降置信、权重重归一化）。
- ✅ 腾讯自选股 westock-data：`npx -y westock-data-skillhub@1.0.5`，无 key，CVM 可用 → 板块异动（`sector ranking`）。
- ✅ 东财全球资讯 7×24：`np-weblist.eastmoney.com/comm/web/getFastNewsList`，零鉴权 → 实时新闻。前端 `NewsStrip.vue` 用 `newsImpact.ts` 规则模板过滤：**仅展示最热前 10 且能推算出板块+利好/利空**的资讯（其余不展示），每条带情绪小圆点。
- ✅ 盈米 yingmi：`yingmi-skill-cli mcp call SearchFunds`，需在 CVM 安装并授权 → 场外基金（未装时优雅降级）。
- ✅ a-stock-data 腾讯财经 `qt.gtimg.cn` 实时行情（CVM 不封 IP，最稳）：
  - A股 ETF/指数盘中 SNAPSHOT 附加可靠源 → `gtimg_client.fetch_realtime` + `collector.collect_realtime_gtimg`（C2）。
  - **美股三大指数** 道琼斯/纳斯达克/标普500（`usDJI`/`usIXIC`/`usINX`，`us` 前缀）→ `gtimg_client.fetch_us_indices` + `collector.collect_us_indices`（C14，首页美股面板）。
- ✅ NeoData金融搜索 / 腾讯自选股-金融数据查询 / US Stock Analysis：**agent 侧查询工具**（方法论研判、临时查证），**不进入后端定时采集管线**；后端自动管线维持 gtimg + akshare(sina) + westock-data + 盈米 + 东财新闻。
- ❌ 富途 futuapi：需本机 OpenD 桌面，CVM 无头不可用，仅本地人工分析，不进自动管线。
接入层集中在 backend/app/services/external_data.py（所有函数对失败返回 available:false 字典，绝不抛 500）。

【已落地（可直接用，勿重复造轮子）】
- P6：同花顺式日 K 线（开高低收 + 成交量双 grid + dataZoom 横向缩放 + 红绿）+ ETF 列表综合分/当日涨幅排序。
- P2：场外基金页（/offexchange）+ GET /api/external/offexchange（盈米，未装 CLI 降级）。
- P3：板块异动页（/sectors-movement）+ GET /api/external/sectors/movement（腾讯自选股）。
- P5：首页横向滚动实时资讯条（NewsStrip）+ GET /api/external/news（东财）。
- C14：首页美股大盘面板（道琼斯/纳斯达克/标普500，gtimg us 通道，`UsIndexTicker.vue`）+ ETF 扩至 48 支（16+29 场内 + 3 场外）并加板块简写（`category` 标签，EtfTable/WatchBoard 名称后显示）。
- C15（hotfix）：修复 C14 切源后 ETF 日 K 重影。`quote_repo.get_bar_history/get_max_bar_timestamp/get_latest_quote` 新增数据源优先级去重（sina > ths > tx > em），避免 em + sina 同交易日 BAR 同时返回导致 K 线重复蜡烛。
- C16（2026-07-26 续作，用户 5 点诉求）：① 板块异动端点/前端补「更新时间」（`SectorMovementOut.generated_at` + `SectorMovement.vue` 用 `toBeijing` 展示）；② 复盘「查看依据」由原始 KV 改为算法生成的专业文字分析——新增 `Opinion.basis_text` 列（幂等 ALTER 补列）+ `opinion_engine/templates.basis_text()`（用 `supporting_metrics` 写市场环境/ETF技术/量价/板块资金/数据完整性叙述）+ 前端 `OpinionList.vue`「查看依据」渲染 `basis_text`，原始 `input_summary` 降为次级「原始信号参数」折叠；③ 诊断并澄清：110020 沪深300ETF联接A 是**场外联接基金**（`seed_mapping.py:75 listing="场外"`），按设计无场内日K线/板块/ETF技术面 → 复盘意见仅由市场宽度+指数环境驱动（综合 50/置信 55/环境 WEAK/仓位 0-0% 看似不变是预期，非 bug）；前端 `EtfDetail.vue` 对 `listing="场外"` 显示「场外联接基金无场内日K线」。④ CVM 板块历史源调查：亲测腾讯 `web.ifzq.gtimg.cn` K 线接口**不支持板块 BK 代码**（仅指数/个股），不可作板块历史源；CVM 那次 10 BK 全失败是旧代码（`preferred="em"`）未 `git pull` 所致，新版走 ths 后 6/8 板块可补（2 个不可映射）。
- C16.2（2026-07-26，hotfix）：修复 ETF 详情页 500——根因是 API 进程 `lifespan` 从不调 `init_db`，CVM 旧库缺 C16 新增的 `opinion.basis_text` 列，`opinion_to_dict` 读该列 → `no such column` → 500。修复：`session.py` 把私有 `_ensure_columns` 提为公共 `ensure_schema_columns()`（表不存在/列已存在均跳过、幂等），`main.py` 在 `lifespan` 用可写引擎（非 query_only 的 read_engine）启动即补列，API 启动自愈，不依赖 worker 先跑。详见 devlog C16.2。
- C17（2026-07-26）：① 板块异动三个 Card 副标题加数据日期；② ETF 列表/详情页加「信号时效」标识（≥2 天标「⚠ 信号 N 天前」，2-3 天 amber/≥3 天 rose），场外基金标注「场外·随大盘」避免误读成"针对该 ETF 的风险预警"；③ 非盘中页刷新 60s→5min（`market.ts` POLL_INTERVAL_MS=300_000 + NewsStrip 同步），盘中详情页(EtfDetail)新增 60s 短轮询（修正此前"分时图每60秒更新"名不副实的注释）；④ 美股三大指数(UsIndexTicker)并入大盘指数(IndexTicker)旁并排（桌面端右列 460px，移动端上下相邻）。**诊断结论**：110003 等场外基金显示「市场风险大/综合50/置信55」是设计内（无自身行情、信号纯由宽基市场环境驱动），非脏数据/算法 bug；信号停在旧日期不更新= CVM `etf-worker` 未运行（残留脏数据不会，信号按交易日 upsert）。详见 devlog C17。
- 测试：backend 243 passed（含 tests/test_us_index.py 等）；前端 pnpm build 通过。

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
| 后端 | FastAPI + SQLite(WAL)，231 测试通过 |
| 前端 | Vue3 + ECharts，pnpm build 通过 |
| 数据源 | 平安已弃用；**东财 em 已于 C14 弃用（preferred=sina）**；腾讯自选股 + 盈米 + 东财新闻 + gtimg(A股+美股) + NeoData(agent侧) |
| 远程仓库 | github.com/DingzhenBOT/jcetf.git，main（C14 已全部推送远程：**`70e61d1`** 功能提交 + 文档同步提交；原远程 `65ba8c2`→`70e61d1`，其余为文档提交。本仓库 main 即远程最新） |
| DESIGN.md | 已入库，随本次推送同步 |

## 三、目录导航

- `backend/app/services/external_data.py` —— 外部 skill 接入层（P2/P3/P5 数据源，**降级契约**所在地）
- `backend/app/api/routers/external.py` —— `/api/external/*` 三个端点
- `backend/app/data_provider/gtimg_client.py` —— 腾讯财经 qt.gtimg.cn 实时行情客户端（盘中 SNAPSHOT 附加源 + C14 美股指数 `fetch_us_indices`）
- `backend/app/collector/collector.py` —— `collect_realtime_gtimg`（collect_market 末尾触发，优雅降级）；`collect_us_indices`（C14，US_INDEX 写入）
- `backend/app/api/routers/market.py` —— `US_INDEX_LABELS` + `market_overview` 填充 `us_indices`（C14 首页美股面板）
- `frontend/src/views/SectorMovement.vue` / `OffExchange.vue` —— 新页面
- `frontend/src/components/sections/NewsStrip.vue` —— 首页资讯条（跑马灯 + 点击弹窗 + 规则影响分析）
- `frontend/src/components/charts/PendulumChart.vue` —— 指数当日涨跌幅摆锤图（首页指数卡/美股条复用）
- `frontend/src/components/UsIndexTicker.vue` —— 首页美股大盘条（C14，展示型不打开 A股抽屉）
- `frontend/src/components/sections/EtfTable.vue` / `WatchBoard.vue` —— ETF 名称后显示板块简写标签（C14，`etfCategory`）
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
7. **CVM 必须先确认 `git pull` 真正生效**（2026-07-26 backfill 实测踩坑）：旧代码（`DataSourceConfig.preferred="em"`）下 `sector_history` 仍先试东财报 `em: ConnectionError`；C14 已改 `preferred="sina"`，em 不进轮转，此时板块应报 `no applicable source`（快速失败）而非触网。若 CVM backfill 仍见 `em:` 报错，说明工作树仍是 C14 前代码——先 `git log -1`（应见 `0bc2005`）+ `git status`（应 clean）+ 确认 `config.py:73 preferred="sina"`，再重跑。
8. **板块历史/资金流在 CVM 为 D4 降级（设计内，非回归）**：em 被 RST 拦截、ths 仅覆盖 6/8 板块、sina/tx 无板块历史实现 → 部分 BK 失败。采集器 `try/except` 捕获（回填照常完成），引擎对缺失板块返回 `None` 并从 composite 剔除（不崩，仅降置信度）。产品核心「板块资金流」信号在 CVM 部分缺失。**已亲测腾讯 `web.ifzq.gtimg.cn` K 线接口不支持板块 BK 代码（仅指数/个股）**，故 gtimg 不可作板块历史源；CVM 板块历史唯一可达源是同花顺 ths（新版 `get_sector_history` 已改走 ths）。CVM 那次 10 BK 全失败是**旧代码（`preferred="em"`）未 `git pull`** 所致——先 `git log -1` 应见 `2368864` + `git status` clean + 确认 `config.py:73 preferred="sina"`，`systemctl restart etf-api` 后重跑 backfill，6 个 ths 覆盖板块应出数据。2 个不可映射板块（医药/消费）属设计内 D4。
9. **盈米「报未初始化」是 root/ubuntu $HOME 不一致（CVM 实测）**：后端 `User=root`（`deploy/etf-api.service`），盈米 apiKey 存 `$HOME/.yingmi-skill-cli/config.json`；初始化多在 `ubuntu` 用户下完成 → 服务端子进程读 `/root/.yingmi-skill-cli` 找不到授权。解法三选一（推荐①）：① `/workspace/config/.env` 加 `YINGMI_HOME=/home/ubuntu` 后 `systemctl restart etf-api`（代码 `_yingmi_env()` 已支持）；② `sudo ln -sfn /home/ubuntu/.yingmi-skill-cli /root/.yingmi-skill-cli`；③ `sudo su -` 后 root 重做 init（需再收短信）。详见 README §3.5。
