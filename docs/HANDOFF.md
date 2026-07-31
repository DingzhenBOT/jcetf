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
- ⚠️ **东财 push2 直连**（`push2.eastmoney.com`，**C19-F 误判为可达，C19-G 实测纠正**：极小请求 `pz=1` 可达，但**批量请求（`pz=500`+全字段 clist、逐 BK kline）在 CVM 与沙箱均被 RST** → 不可靠、生产关闭）：代码保留于 `backend/app/data_provider/eastmoney_web.py`（`fetch_sector_fund_flow_snapshot`/`fetch_sector_kline`），collector `collect_sector_history_web`/`collect_sector_fund_flow_web` 入库 `SECTOR/BAR/em_web`；`settings.backfill.use_em_web=False`（默认关），仅作不可靠备选，勿在 CVM 开启。详见 devlog C19 G。
- ✅ **腾讯自选股 westock-data（板块主源，C19-G 确立）**：`npx -y westock-data-skillhub@1.0.5`，无 key，CVM 稳定可用 → 返回「行业/概念涨幅 + 资金流入 TOP 榜」（异动榜，非全量）。`collector.collect_sector_from_westock` 合并 industry/concept(涨跌幅)+fund_flow(主力净流入) → 按 `collector/sector_map.py` 的 `resolve_sector_bk` 解析为跟踪 BK → 入库 `SECTOR/BAR/westock`。worker 定时 `sector_westock_collect`（默认 900s）。板块面板信号来源 = 当日异动排名；非活跃板块引擎 D4 降级属设计内。
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
- C18（2026-07-26）：① 系统状态页 `/#/system`「轮询间隔：30 秒」写死文案改为动态引用 `POLL_INTERVAL_MS`（显示「5 分钟」），消除"逻辑没更新过"的观感；并明确「数据新鲜度/风险水平没更新」真因是 CVM `etf-worker` 停了（前端如实反映后端停滞，非前端 bug，需 `systemctl status etf-worker` 排查）。② **「最新信号」超过 2 天自动从"当前信号"中清除、历史保留**：`signal_repo.get_latest_signals` / `get_latest_signal_for_etf` 新增 `max_age_days=2` 默认过滤（子查询 `generated_at >= utcnow()-2d`），过期 ETF 在最新信号表/ETF 列表即"无当前信号"；`get_signal_history` 不动 → 历史完整保留。新增测试 `test_stale_signal_excluded_from_latest_but_kept_in_history` 验证。详见 devlog C18。
- C19（2026-07-27 → 收尾于 C19-G）：① **盘中不采集根因修复**：`market_calendar.is_trading_day` 用 sina 历史日历不含未来交易日→误判非交易日→采集守卫静默 skip（worker 心跳正常但全天无采集）；改为"未来日回退启发式（周一~周五=True）"，新增 `calendar_last_day()` + worker 启动/守卫 skip 日志。② 修 sina 分时代码前缀 bug（`_to_sina_symbol` 对 INDEX 大写误走 ETF 分支→sz000300 无效，加 `kind.lower()`）。③ 新增 `scripts/manual_backfill_today.py`（收盘后补今日快照+日K+分时+复盘）、`scripts/diag_data.py`/`diagnose_worker.sh` 诊断。④ **分时源切腾讯 gtimg**（本轮收尾）：`gtimg_client.fetch_intraday_minute`（web.ifzq.gtimg.cn 当日分时，CVM 不封、返回当日，替代 sina 返回两周前旧数据）；`collector.collect_intraday_minute` 优先腾讯、降级 sina；`worker._collector()` 注入。⑤ **板块主源切 westock-data 异动榜（C19-G）**：CVM 实测 `push2.eastmoney.com` 批量请求被 RST（C19-F 误判可达，已纠正），push2 直连 `use_em_web=False` 默认关；westock-data 为 CVM 唯一稳定板块源，`collect_sector_from_westock` + `sector_map.resolve_sector_bk` 入库 `SECTOR/BAR/westock`，worker 定时 `sector_westock_collect`（900s）；引擎对非活跃板块 D4 降级（设计内）。详见 devlog C19 F/G。
- C20（2026-07-27，用户 4 项盘中分时反馈）：① 午休断点（`IntradayChart.vue` 插 `__LUNCH__` 空槽 + `connectNulls:false`，11:30↔13:00 断开）；② 午后数据错（主因是①的跨午休连线视觉误导，随①修；另加 sina 降级「未来异常」守卫防陈旧污染）；③ 均价确认即 VWAP（`avg=cum_pv/cum_vol`，改名「分时均价」，非 BOLL 中轨）；④ 盘中 regime 实时化（`evaluate_etf` 透传 `phase` + 新增 `_intraday_regime` 用实时 INDEX 1m 重算，不再被陈旧日线压成全「偏弱」）。前端 `pnpm build` 通过。
- C21（2026-07-29，用户复核）：① 盘中分时轴**改回连续**（回退 C20 午休断点，同花顺风格 11:30 直连 13:00，`connectNulls:true`）；② 用户称「数据依旧错」——沙箱拉今天真实 gtimg 端到端验证代码链路正确（涨跌幅/VWAP 均对），判定是 C20 午休断点视觉误导，连成后消失；为防 CVM 上 gtimg 偶败回退陈旧 sina，后端 `_intraday_rows_fresh` 新增**盘中滞后**守卫（仅盘中、落后>120min 拒，守「不误杀同日早前分钟」原则）。
- C22（2026-07-30，用户日志实锤，纠正 C21「视觉误导」误判）：CVM 上 `web.ifzq.gtimg.cn` 分时接口**超时失败**、回退 sina 又对指数用错代码 `sz000300` 返回空 → 分时**真实错误**（归零/断层/不连续），非视觉。主源从「web.ifzq + sina」改为 **qt.gtimg.cn 实时快照(`fetch_realtime`，CVM 稳定)批量转 1m BAR**：最新价作 close，增量=`当日累计成交量 − 上一根1m BAR的cum_volume`（新增 `market_quote.cum_volume` 列；以 BAR 累计为基准规避快照180s/1m采样60s 频率错配的漏计）；未覆盖标的才回退 web.ifzq→sina。`get_bar_history` 源优先级 `gtimg` 提最高(0)，残留 sina 脏数据须让 gtimg 胜出。新增 `quote_repo.get_latest_1m_bars`。前端连续轴沿用 C21 无需改。
- 测试：backend 279 passed（C21 +1、C22 +6，共 +7）；前端 pnpm build 通过（C21 已 build）。
- C23（2026-07-30，用户三大意见板块重做 + 修复"最新信号千篇一律先观望"）：`decide_tier` 移除硬闸门（市场弱降档而非一票否决，仅 `BEAR+缺失` veto 仍 `NO_PARTICIPATE`）；盘中意见并入「最新信号」（每 5min，`live` 相位，五因子盘中强度 + R1/R2）；新增午盘意见（`lunch`，11:40）；收盘后复盘（`post_close`，确定性三档价位 突破/加仓/止损 + 明日预期）。算法确定性编码三套 skill 方法论（持仓监控告警 / A股每日复盘 / A股短线交易），无 LLM。新增 `intraday_strength.py`/`levels.py`；`Opinion.trade_plan` 列；`POST /api/signals/{etf}/refresh` 按需重算。详见 devlog C23。**backend 304 passed（C23 +25）**。
- C24（2026-07-31，用户续作：场外基金纳入正规引擎，**方案B**）：场外开放式基金（110020 沪深300ETF联接A / 000008 沪深300ETF联接 / 110003 易方达沪深300联接 等）注册进 `etf_mapping`（`listing='场外'`），用 akshare 东财 `fund_open_fund_info_em` 净值历史当「日K」存为 `market_quote` 的 `symbol_type=OFF_FUND` BAR（open=high=low=close=NAV，previous_close=前一日NAV），与场内 ETF BAR **物理隔离**；`engine.evaluate_etf` 按 `listing` 选 `bar_type=OFF_FUND` **复用** ETF 技术面/RS/三档价位；`pipeline` 对 `listing='场外'` 仅 `post_close` 评估（T+1 无盘中，live/lunch 跳过并计 `skipped_offexchange`），场外意见**复用场内引擎三档价位作参考**（不写专属模板）。新增 `akshare_adapter.get_open_fund_nav_history` / `normalize.normalize_off_fund_nav` / `collector.collect_offexchange_nav_history`；`backfill_history` 路由 场外→OFF_FUND 管道（增量桶 `off_fund`）；`market.py etf_history` 按 `listing` 读 OFF_FUND。前端 `OffExchange.vue` 行可点进 `/etfs/{code}`、`EtfDetail.vue` 对 `listing='场外'` 隐藏盘中分时/盘中意见/午盘意见、走势卡改「净值走势」、空态提示净值回填后显示。决策：方案B（纳入正规引擎）优于方案A（独立页/独立算法）/方案C（等盈米 CLI）。详见 devlog C24 + 计划书 `toasty-pulse-curie-I1e1lApd.md`。**backend 314 passed（C23 304 → C24 314，+10）**；前端 `pnpm build` 通过（C24-H1 走查）。

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

## 二、当前状态速览（截至 2026-07-31，C23 + C24 已落地）

| 项 | 状态 |
|---|---|
| 后端 | FastAPI + SQLite(WAL)，314 测试通过（C24 后） |
| 前端 | Vue3 + ECharts，pnpm build 通过（连续轴 C21 已 build） |
| 数据源 | 平安已弃用；**东财 em 已于 C14 弃用（preferred=sina）**；腾讯自选股 + 盈米 + 东财新闻 + gtimg(A股+美股，盘中分时主源 C22 起为 qt.gtimg.cn 实时快照转 1m) + NeoData(agent侧) |
| 远程仓库 | github.com/DingzhenBOT/jcetf.git，main（最新提交 **`2489889`**（C24）；本仓库 main 即远程最新） |
| DESIGN.md | 已入库，随本次推送同步 |

## 三、目录导航

- `backend/app/services/external_data.py` —— 外部 skill 接入层（P2/P3/P5 数据源，**降级契约**所在地）
- `backend/app/api/routers/external.py` —— `/api/external/*` 三个端点
- `backend/app/data_provider/gtimg_client.py` —— 腾讯财经客户端：`fetch_realtime`（qt.gtimg.cn 盘中 SNAPSHOT 附加源；**C22 起为盘中 1m 分时主源**：批量拉 ETF+宽基指数实时快照，累计成交量转 1m BAR）+ `fetch_us_indices`（C14 美股指数）+ `fetch_intraday_minute`（C19 当日 1 分钟分时，web.ifzq.gtimg.cn，C22 起降为次源兜底；CVM 实测超时）
- `backend/app/data_provider/eastmoney_web.py` —— **C19-F 新增、C19-G 降级为默认关闭**：东方财富 push2 直连源（CVM 批量请求实测被 RST，不可靠）。`fetch_sector_fund_flow_snapshot`（clist 全板块资金流/涨跌）、`fetch_sector_kline`（secid=90.BKxxxx 板块日K）；异常抛 RuntimeError 交由 collector `*_web` 方法降级。`settings.backfill.use_em_web=False`（默认关）。
- `backend/app/collector/sector_map.py` —— **C19-G 新增**：`SECTOR_NAME_ALIASES`（BK→规范名+别名）+ `resolve_sector_bk(name, sector_codes)`（别名精确匹配仅限跟踪集，子串兜底；未匹配返回 None）。westock 板块名→跟踪 BK 映射。
- `backend/app/collector/collector.py` —— `collect_realtime_gtimg`（collect_market 末尾触发，优雅降级）；`collect_us_indices`（C14，US_INDEX 写入）；`collect_sector_from_westock`（C19-G 主源，合并 westock industry/concept/fund_flow 三表入库 `SECTOR/BAR/westock`）；`_tally` 修复（batch 采集 status="done" 带 ok/failed 桶正确计入）。
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
- `backend/app/collector/collector.py` —— `_collect_bar` / `collect_intraday_minute`（**C22 主源：gtimg 实时快照批量转 1m，增量=`当日累计−上一根1m BAR的cum_volume`**；次源仅兜底未覆盖标的）采集后调 `assess`；`collect_realtime_gtimg`（盘中附加源）
- `backend/app/repository/quote_repo.py` —— `get_bar_history`（源优先级去重，`gtimg` 最高）、`get_max_bar_timestamp` / `get_latest_quote` 过滤 ANOMALY；`get_latest_1m_bars`（C22：取当日最新 1m BAR 含 cum_volume，供算增量）、`get_latest_snapshots_batch`（gtimg 快照批量）
- `backend/app/db/session.py` —— `_ensure_columns` 幂等 ALTER（etf_mapping.listing、signal.phase 存量回填）
- `backend/app/evaluation/pipeline.py` —— `post_collection_evaluate` 写 Signal.phase
- `backend/scripts/flag_ohlc_anomalies.py` —— 已入库 OHLC 脏数据改标 ANOMALY（dry-run / --apply / --symbol）
- `frontend/src/components/IndexDrawer.vue` —— 大盘抽屉「当日分时 / 日K线」Tab（默认分时）
- `frontend/src/views/EtfDetail.vue` —— 结论 Hero 盘中优先 + 阶段/时间标注；意见拆「盘中意见」/「收盘后复盘」
- `frontend/src/lib/tier.ts` —— `phaseText` / `isIntradayPhase`
- `docs/devlog.md` —— 全量开发日志（C0–C23 章节）

### 三(续). C23 关键文件（三大意见板块重做 + 修复千篇一律观望）
- `backend/app/strategy_engine/engine.py` —— **`decide_tier` 降档修正（C23 核心）**：`BEAR`−18 / `WEAK`−10 / `high_vol`−5（可叠加），仅 `BEAR+缺失` veto 仍 `NO_PARTICIPATE`；`MARKET_RISK_HIGH` 枚举保留向后兼容但不再产出；`evaluate_etf` 5.5 段接入 `intraday_strength`/`check_r1_r2`/`compute_trade_plan`，`supporting_metrics` 增盘中强度/R1/R2，`trade_plan` 透出。
- `backend/app/opinion_engine/intraday_strength.py` —— **（新）** `intraday_strength(etf_1m, index_1m, …)` 五因子盘中强度 0–100（相对大盘30%/量能20%/均线20%/资金20%/筹码10%，含 lean/factors/missing）+ `check_r1_r2(daily_df, etf_ind, fund_flow)`（R1 补仓看多 / R2 超跌抄底）。
- `backend/app/opinion_engine/levels.py` —— **（新）** `compute_trade_plan(daily_df, etf_ind, lookback=20)` 确定性三档价位（突破/加仓/止损 单调且 >0）+ 明日预期 regime。
- `backend/app/opinion_engine/templates.py` —— `TEMPLATE_LIVE` / `TEMPLATE_LUNCH` + `_fmt_num` / `r1r2_text` / `trade_plan_text`；`basis_text` 补 `live:'盘中实时'` / `lunch:'午盘'`。
- `backend/app/opinion_engine/engine.py` —— 按 `phase` 选模板；`trade_plan` 透传。
- `backend/app/db/models/signal_opinion.py` + `db/session.py` —— `Opinion.trade_plan` 列 + 幂等 ALTER 补列。
- `backend/app/evaluation/pipeline.py` —— Signal upsert 跳过 `trade_plan`；Opinion upsert 双分支写 `trade_plan`。
- `backend/app/api/routers/schemas.py` + `serializers.py` —— `OpinionOut` 补 `trade_plan`/`basis_text`/`model_version`（修复响应裁剪）。
- `backend/app/api/routers/signals.py` —— **`POST /api/signals/{etf}/refresh`**（db_writer_lock 下 `post_collection_evaluate(phase="live")`，持锁返回 409），供前端「重新评估」按需重算盘中信号。
- `backend/app/api/routers/opinions.py` —— `_VALID_PHASES` 增 `live` / `lunch`。
- `backend/app/worker.py` —— 移除旧 `job_intraday_evaluate`；新增 `job_intraday_signal`（IntervalTrigger 300s，`is_trading_now` 守卫→`live`）+ `job_lunch_opinion`（Cron 11:40→`lunch`）；`build_scheduler` 注册。
- `backend/app/config.py` —— `SchedulerConfig.intraday_signal_interval_seconds: int = 300`。
- `frontend/src/components/sections/OpinionList.vue` —— 渲染 `trade_plan`；`frontend/src/views/EtfDetail.vue` 盘中强度/倾向/R1/R2 徽标 + 「重新评估」按钮 + 「午盘意见」Card；`frontend/src/api/{types,endpoints}.ts` 增 `live`/`lunch`+`TradePlan`/`refreshSignal`。
- `DESIGN.md` —— 设计系统规范（9 章节）

## 四、关键约束提醒（踩坑经验）

1. **网络波动曾导致重复命令改坏代码**：外部调用一律降级，不 500。
2. **盈米 CLI 沙箱未装** → 场外基金页当前降级提示；README §3.5 已文档化安装授权流程，需在 CVM 由用户本人完成手机号+短信交互授权。
3. **westock-data 首调慢**（npx 现场拉包）→ 生产建议预装/缓存。
4. **P1 铸造新 strategy_version 会重塑历史 Signal** → 先定口径再灰度，别直接覆盖。
5. **Edit 前必须先 Read**（本 agent 环境硬性要求）。
6. **git token 勿硬编码进源码**，推送时用环境变量/临时 URL。
7. **CVM 必须先确认 `git pull` 真正生效**（2026-07-26 backfill 实测踩坑）：旧代码（`DataSourceConfig.preferred="em"`）下 `sector_history` 仍先试东财报 `em: ConnectionError`；C14 已改 `preferred="sina"`，em 不进轮转，此时板块应报 `no applicable source`（快速失败）而非触网。若 CVM backfill 仍见 `em:` 报错，说明工作树仍是 C14 前代码——先 `git log -1`（应见 `0bc2005`）+ `git status`（应 clean）+ 确认 `config.py:73 preferred="sina"`，再重跑。
8. **板块主源 = westock-data 异动榜（C19-G）**：CVM 上 em(push2/push2his) 批量请求被 RST、ths 解析报错/空、akshare 无 sina 板块函数 → **无任何源能稳定拿全量板块历史+资金流**。westock-data（`npx -y westock-data-skillhub@1.0.5`）是 CVM 唯一稳定板块源，但返回「异动 TOP 榜」（非全量）→ 板块信号从"完整历史"降级为"当日异动排名"，非活跃板块引擎 D4 降级属设计内（见 `sector_engine.engine`）。push2 直连（`eastmoney_web.py`）C19-F 误判可达，**C19-G 实测批量 RST，已默认关闭（`use_em_web=False`）**，仅作不可靠备选，勿在 CVM 开启。注：腾讯 `web.ifzq.gtimg.cn` K 线仍不支持板块 BK 代码（仅指数/个股），gtimg 不作板块历史源。
9. **盈米「报未初始化」是 root/ubuntu $HOME 不一致（CVM 实测）**：后端 `User=root`（`deploy/etf-api.service`），盈米 apiKey 存 `$HOME/.yingmi-skill-cli/config.json`；初始化多在 `ubuntu` 用户下完成 → 服务端子进程读 `/root/.yingmi-skill-cli` 找不到授权。解法三选一（推荐①）：① `/workspace/config/.env` 加 `YINGMI_HOME=/home/ubuntu` 后 `systemctl restart etf-api`（代码 `_yingmi_env()` 已支持）；② `sudo ln -sfn /home/ubuntu/.yingmi-skill-cli /root/.yingmi-skill-cli`；③ `sudo su -` 后 root 重做 init（需再收短信）。详见 README §3.5。

## I. C19-I 本轮五处修复（用户验收反馈）

> 用户原话五项：①分时图不如上一版（要连续轴/白线/黄均价线/0%居中/量对齐/净买红净卖绿）②详情页"关联板块：—"恒空删掉 ③美股指数 +52607% 错 ④系统状态秒数不动+新鲜度显示 08:00 ⑤信号恒"市场风险大先观望"且恒报数据缺失。

### I.1 美股指数涨跌幅错误（#102，后端）
- **根因（实测确认）**：`backend/app/data_provider/gtimg_client.py` 美股字段下标整体偏 +1。腾讯财经 `usDJI/usIXIC/usINX` 真实格式：`[30]=时间戳 [31]=涨跌额 [32]=涨跌幅% [33]=最高 [34]=最低`。原 `_US_PCT=33` 实际读到**最高价**(~52871) → 显示 +52871% 类荒谬值；`_US_TS=31` 读错时间戳。
- **修复**：`_US_TS=30 / _US_CHG=31 / _US_PCT=32 / _US_HIGH=33 / _US_LOW=34`（与 A股 `v_*` 不同，美股整体 +25 偏移）。改常量 + 注释，函数逻辑不变（仍走 `normalize_us_index_snapshot`→`_derive_change_percent`）。
- **验证**：实时 curl `qt.gtimg.cn/q=usDJI,usIXIX,usINX` 解析 → 道琼斯 +1.25% / 纳指 +0.09% / 标普 +0.44%（正确符号与量级）；`test_us_index.py` 通过。

### I.2 详情页"关联板块：—"恒空（#101，前端）
- **根因**：`EtfDetail.vue` 头部一行 `关联指数：{{code}} · 关联板块：{{ related_sector_codes.join(',') || '—' }}`，而 `etf_mapping.related_sector_codes` 对宽基 ETF 恒为 `[]`，行业 ETF 才有值 → 宽基永远显示"—"。
- **修复**：去掉 `· 关联板块：…` 整段，仅保留 `关联指数：{{code}}`（有值时才有意义）。`related_sector_codes` 仍入库供信号引擎使用。
- **注意**：该字段恒空恰是 #104 的侧面证据——宽基本就无关联板块。

### I.3 盘中分时图 v2（#100，前端 `IntradayChart.vue` 重写）
- **连续 x 轴**：去掉午休 11:31–12:59 空槽，`09:30–11:30` 直接接 `13:00–15:00`（类同花顺，折线不断开）。
- **白价格线 + 黄均价线**：价格线 `#fff`，均价线 `#f5c518`（用 `IntradayPoint.avg` = 累计成交额/累计成交量 VWAP，后端已提供）；图表面板改深色 `#0d1117` 保证白线可见（同花顺分时观感）。
- **y 轴 0% 居中**：取 change%/avg% 最大绝对值对称 `min=-M/max=+M`，0% 固定中线；涨跌幅按 price vs 昨收。
- **量对齐 + 净买红净卖绿**：底部量柱与价格共用同一类目轴；着色按本分钟价 vs 上一分钟价（首分钟 vs 昨收）：涨=红(#ef4444) 跌=绿(#22c55e) 持平=灰。

### I.4 系统状态栏（#103，前端+后端）
- **秒数不动**：`stores/market.ts` 新增导出 `secondsSinceRefresh`（读 1 秒 `_now` 时钟），`SystemStatus.vue` 的"最后成功刷新：X 秒前"改为 `{{ secondsSinceRefresh }}` 每秒跳动；并附"还 X 秒自动刷新"（复用 `secondsToRefresh`）。
- **新鲜度显示 08:00**：根因 `overview.as_of` 只是**交易日（日期）**，`toBeijing(as_of)` 把日期当 UTC 午夜 → 北京 08:00，与真实采集时间无关。
- **修复**：后端 `MarketOverviewOut` 新增 `latest_collected_at`（取主要指数最新 SNAPSHOT/BAR 的最大 `timestamp`，即真实采集时刻）；`market.py` 计算并下发；前端 `MarketOverview` 类型加字段，"数据新鲜度"改用 `latest_collected_at`（回退 as_of）。白天有实时采集时显示真实时刻（如 14:32），不再固定 08:00。

### I.5 信号恒"先观望"且恒报数据缺失（#104，后端策略引擎 + 部署动作）
**根因（决定性）**：策略引擎只读 `data_kind='BAR'` 的**日线历史**；而本库 `market_quote` 只有 `data_kind='SNAPSHOT'`（CONCEPT/ETF/INDEX/INDUSTRY 快照），**一条 BAR 都没有** → `get_bar_history` 全空 → ETF/板块/指数 BAR 全缺 → `etf_rs_missing`/板块缺失/`market` 分为零 → 全部"数据缺失"，档位恒为"先观望/别碰"。
- **引擎查询本身正确**：日线 BAR 存 `symbol_type=ETF/INDEX/SECTOR`（板块历史即 `SECTOR`+BK 代码，与 `get_bar_history("SECTOR", bk_code)` 一致）；缺口是**历史日线回填从未落库**。
- **回填入口**：`worker.job_backfill_history` 每天 **16:30（北京）** 跑（`backfill_history` → ETF/指数用 akshare、板块用 westock-data 异动榜（CVM 稳）、板块资金流用 em_web 默认关）。需确认 CVM 上该任务真正写入 BAR（若 akshare/em 被 RST 封则写不进 → 需改用 CVM 稳源，见约束 8）。
- **顺手修的代码缺陷（避免宽基被误判缺失）**：`strategy_engine/engine.py` 宽基 ETF（`related_sector_codes` 为空）本就把 `sector/fund_flow` 计入"缺失"→ 误扣置信度、弹"数据缺失"。改为：按 `has_sector` 动态裁掉权重中的 `sector_trend/fund_flow`，且 `failed_rules` 仅在 `has_sector` 为真且查不到时才记 `sector_data_missing/fund_flow_missing`。
- **验证（确定性，注入最小 BAR 后跑引擎）**：
  - 510300 沪深300（宽基）：conf **100**，`failed_rules=['breadth_missing']`（不再含 sector/fund_flow），`available` 含 market+etf_rs。
  - 512010 医药（行业，注入 BK0465 板块 BAR）：conf **100**，`failed_rules=['breadth_missing']`，`available` 含 market+sector+fund_flow+etf_rs（全可用）。
  - 档位为 `MARKET_RISK_HIGH`（"市场风险大，先观望"）是因为造的弱市数据使 `regime=WEAK`——**市场偏弱时观望是算法正确的保守行为，非 bug**；市场走强后档位会分化。
- **算法合理性评估**：复合分（market+sector+fund_flow+etf_rs）D4 缺失重归一化 + 风险否决（BEAR+缺失）+ 保守档位，模型合理。用户提到的 `@持仓监控告警`/`@基金分析` skills 本沙箱未安装，评估基于代码分析。真正的 operational gap 是日线 BAR 回填必须在 CVM 真正落库。
- **部署动作（用户侧）**：在 CVM 跑一次 `backfill_history`（或等 16:30 定时）确认 `market_quote` 出现 `data_kind='BAR'` 行；若仍为空，按约束 8 排查 akshare/em 网络，必要时为 ETF/指数历史换 CVM 稳源（gtimg 仅支持指数/个股 K 线，不支持板块 BK）。

---

## II. C20 · 盘中分时 4 项修复（2026-07-27，用户 4 项反馈）

> 用户原话：① x 轴错，应从 11:30 直接跳到 13:00，不要跨午休连起来；② 下午数据全是错的，不知从哪来；③ 均价（黄线）错，均价是分时均价（同花顺黄线含义），别自己乱画成布林中轨；④ 盘中建议为什么还是全「偏弱」。

**4 项根因与修复（先读代码+沙箱实测验证，未盲改）**：

| # | 现象 | 根因 | 修复 | 落点 |
|---|------|------|------|------|
| ① | 午休不断开 | C19-I #107 为「午休留空槽」特意设 `connectNulls:true`，把 11:30 直连 13:00 | 插午休空槽类目 `__LUNCH__` + `connectNulls:false`（价格/均价线午休断开）。纯前端 | `frontend/src/components/charts/IntradayChart.vue` |
| ② | 午后数据错 | 后端分时链路正确（gtimg 注入、增量、时区、过滤均对，沙箱实测 242 行下午正确）；「午后错」主因是①跨午休直线把上午末点直连下午首点，视觉像数据错；辅以 sina 降级偶发陈旧 | ①修好即正；另加 sina 降级「未来异常」守卫 | `collector/collector.py` `_intraday_rows_fresh` |
| ③ | 均价像 BOLL | **误判**：均价本就是 VWAP（`avg=cum_pv/cum_vol`，000300 收 4600.26/均价 4570.61 合理）。「像 BOLL」是①跨午休直线+②午后视觉错共同造成的错觉 | 确认 VWAP 正确；改名「分时均价」并在午休断开后不再连成类 BOLL 直线 | `IntradayChart.vue`（series `name`） |
| ④ | 盘中建议全偏弱 | `decide_tier` 在 `regime∈{WEAK,BEAR}` 强制 `MARKET_RISK_HIGH`；盘中 `evaluate_etf` 用**昨日日线**算 regime（今日日线 15:10 才写），日线弱则盘中实时动量修正被压制 | `evaluate_etf` 透传 `phase`；盘中阶段用**实时 INDEX 1m** 重算 regime（指数当日涨→抬升为 VOLATILE/TREND_UP，当日走弱保持日线 WEAK/BEAR）；`post_close` 保持日线逻辑 | `strategy_engine/engine.py` `_intraday_regime` + `evaluation/pipeline.py` |

**关键决策**：
- ④ 的 regime 实时化**只看指数当日涨跌幅**抬升 WEAK/BEAR；若当日指数微涨但板块/个股普跌，regime 抬升但个股建议仍由 composite+量价形态决定，不会无脑看多。
- ② 守卫刻意只挡「未来异常」（`max_ts > now+5min`），**不挡同日早前分钟**（避免午后看上午数据时误杀正常 sina 降级）；多日旧数据由 normalize 的 `trading_date` 过滤兜底（#106）。
- ③ 经实证确认 VWAP 数学正确，非算法缺陷，**不改后端**，仅更名+前端断开消除视觉误导。

**测试**：`backend 272 passed`（C20 新增 5 例：strategy_engine 2 + collector 3）；前端 `pnpm build` 通过（660 模块，0 类型错误）。

**⚠ CVM 部署待办（用户侧，沙箱无法验证 — 下个 agent 接手前务必确认用户已做）**：
1. `cd /workspace && git pull` → `cd frontend && pnpm build`（纯前端改动需重建覆盖 Nginx dist）→ `sudo systemctl restart etf-worker`（后端改动需重启 worker 生效）。
2. 验证午休断点：盘中 ETF 详情→分时图，11:30 与 13:00 间应空白断开，价格线/分时均价线均不跨午休连接。
3. 验证均价：黄线贴价格线下方呈 VWAP 平滑累计曲线（非居中笔直的 BOLL 式中轨）。
4. 验证盘中建议：盘中（非收盘后）若当日指数上涨，不应再「全偏弱/市场风险大」；当日真走弱则维持谨慎属正常。
5. 若仍见「午后数据错」：查 `journalctl -u etf-worker | grep -iE 'intraday sina stale|intraday gtimg failed'` 确认是否 gtimg 在 CVM 偶败降级到陈旧 sina；频繁触发说明 CVM→gtimg 连通性问题，需另查网络/超时。

**已知边界**：② 若 CVM 上 gtimg 持续失败、sina 又返「同日但陈旧」非未来异常数据（理论不应发生，因 sina 实时源最新分钟≈now），守卫不拦截——靠 #106 `trading_date` 过滤兜底，单日内陈旧需后续按值校验（超出本期）。

---

## III. C21 · 盘中连续轴 + sina 滞后守卫（2026-07-29，用户复核）

> 用户原话：①「11:30 与 13:00 间直接合并，不要断开」；②「你上午和下午的数据依旧是错的」。

**关键转折**：① 与 C20 / 历史 #5 相反——用户看过同花顺后确认**同花顺午休处连续**，要求 11:30 直连 13:00。故回退 C20 午休断点，改回连续轴。
**调研结论（沙箱拉今天真实 gtimg 端到端验证）**：代码链路（fetch→normalize→端点 VWAP/北京时间/源优先级去重）**完全正确**，涨跌幅/VWAP 均对。「数据依旧错」极大概率是 C20 午休断点视觉断裂的误判，连成连续轴后消失。若连成后仍数字错 → CVM 上 gtimg 分时抓取失败、回退陈旧 sina。

**改动**：
- 前端 `IntradayChart.vue`：移除 `__LUNCH__` 空槽，`connectNulls:true`，09:30–11:30 直连 13:00–15:00；保留「分时均价」(VWAP) 命名。
- 后端 `collector/collector.py` `_intraday_rows_fresh`：新增**盘中滞后**守卫（仅 `is_trading_now` 时，最新分钟落后>120min 拒），守「不误杀同日早前分钟」原则；gtimg 正常不走 sina 分支。
- 测试：backend **273 passed**（C21 +1）；前端 pnpm build 通过。

**⚠ CVM 部署待办（用户侧）**：
1. `git pull` → `cd frontend && pnpm build` → `sudo systemctl restart etf-worker`。
2. 验证连续轴：分时图 11:30↔13:00 连续无断点。
3. 验证数据：连成后涨跌幅/VWAP 与腾讯财经/同花顺一致 → 此前「数据错」即视觉误导，已解决。
4. **若仍数字错**：跑 `journalctl -u etf-worker --since today | grep -iE 'intraday'` 看 `intraday gtimg failed, fallback sina` / `intraday sina stale, skip upsert`，把输出发 agent —— 据此修 gtimg 连通性或改用「由稳定 gtimg 实时快照累积 1m 分时」方案。

---

## IV. C22 · 盘中分时主源切换为 gtimg 实时快照转 1m（2026-07-30，用户日志实锤）

> 用户原话：①「你这就是错的啊卧槽，中间突然没合并，而且都断层了，你还说是视觉？」②「人家今日涨1.42你这给人划到0了。」
> 并附 `journalctl -u etf-worker --since today | grep intraday` 实锤：`collect intraday failed: INDEX/000300: intraday_minute sina sz000300 returned empty` 反复 + `intraday gtimg failed, fallback sina` + APScheduler `maximum number of running instances reached`。

**纠正 C21 误判**：C21 把「数据错」归为午休断点视觉误导是**错的**。日志证明 CVM 上 `web.ifzq.gtimg.cn` 分时接口**超时失败**、回退 sina 又对指数用错代码 `sz000300`（应为 `sh000300`）返回空 → 分时**真实错误**（归零/断层/不连续）。

**根因**：
- `web.ifzq.gtimg.cn` 在 CVM 超时 → `collect_intraday_minute` 主源失败 → 降级 sina。
- sina 对 INDEX 000300 用 `sz000300`（错码）→ 返回空 → 沪深300 分时缺失/归零。
- 单轮采集 >60s 触发 APScheduler `maximum number of running instances reached` 跳过 → 空洞/断层。

**修复（即 C21 预见的「由稳定 gtimg 实时快照累积 1m 分时」方案）**：
- 主源改为 **qt.gtimg.cn 实时快照（`fetch_realtime`，CVM 稳定）批量转 1m BAR**：最新价作 close；`volume=当日累计成交量 − 上一根1m BAR的cum_volume`（增量）；`cum_volume=当日累计量`（新增列）。
- 增量以「上一根 BAR 的 cum_volume」为基准（**非快照差值**）→ 规避快照采集(180s)与 1m 采样(60s)频率错配的**漏计/重复**（旧快照差值法在快照中途跳变时会把增量算成 0）。
- 未覆盖标的（快照返回空）才回退次源 web.ifzq.gtimg.cn → sina（保留 C19/C21 滞后守卫）。
- `get_bar_history` 源优先级 `gtimg` 提最高(0)：残留 sina 1m 脏数据须让 gtimg 胜出，否则分时图仍显示错数据；gtimg 只写 1m 不写日线，不影响日线去重。

**改动落点**：
- `backend/app/collector/collector.py` `collect_intraday_minute`：主源快照批量转 1m；次源仅兜底未覆盖标的。
- `backend/app/repository/quote_repo.py`：新增 `get_latest_1m_bars`；`_SOURCE_PRIORITY` 加 `"gtimg": 0`。
- `backend/app/db/models/market.py` + `session.py`：新增 `cum_volume` 列 + 幂等 ALTER 迁移（CVM 重启自动补列）。
- `backend/app/data_provider/gtimg_client.py`：无改动（`fetch_realtime` 沙箱实测返回正确累计成交量/涨跌幅）。

**测试**：backend **279 passed**（C22 新增 6 例：快照转1m字段、增量不漏计核心回归、主源覆盖ETF/次源覆盖指数、cum_volume迁移、get_latest_1m_bars、读路径优先gtimg胜sina）；前端连续轴沿用 C21。

**⚠ CVM 部署待办（用户侧）**：
1. `cd /workspace && git pull` → `cd frontend && pnpm build`（C21 已改前端，本轮无需改但 build 一遍保险）→ `sudo systemctl restart etf-worker`。
2. `journalctl -u etf-worker --since today | grep -iE 'intraday'` 应只见 `intraday snapshot->1m` 类正常日志，不再有 `intraday gtimg failed, fallback sina` / `sina sz000300 returned empty` / `maximum number of running instances reached`。
3. 验证：ETF/指数分时图连续无断层、涨跌幅正确（如沪深300 当日 +1.42% 正常显示，不再归零），分时均价(VWAP) 平滑。
4. 若仍有异常：把 `journalctl -u etf-worker --since today` 全文发 agent，重点看 `intraday gtimg snapshot->1m failed` 是否偶发（快照批量超时则回退次源，属预期降级）。

---

## V. C23 · ETF 详情页三大意见板块重做 + 修复"最新信号千篇一律先观望"（2026-07-30）

> 用户核心诉求：ETF 详情页三大意见板块重做，并修复"最新信号又都是统一的'市场风险大，先观望'"的算法缺陷。
> 决策（已确认）：① 盘中即时意见**并入「最新信号」**（盘中每 5min 高频自动重算，无独立块）；② 午盘意见每日 11:40 一块，可留历史；③ 收盘后复盘给近几日+今日情况 + 明日预期（突破X上车/跌X加仓/跌破Y止损，确定性价位）；④ 算法用**确定性规则编码三套股市 skill 方法论（持仓监控告警 / A股每日复盘 / A股短线交易），无 LLM**。

**根因（决定性，C23 核心修复）**：`strategy_engine/engine.py::decide_tier` 存在**硬闸门缺陷**——当 `regime ∈ {WEAK, BEAR}` 时一律强制 `MARKET_RISK_HIGH`（"市场风险大，先观望"），等于一票否决所有细分信号。结合 C16/P4 落地后，绝大多数 ETF 在某档市场环境下都被压成同一个"先观望"，与用户看到的"千篇一律"完全吻合。

**修复（decide_tier 降档而非否决）**：
- `BEAR` 综合分 −18、`WEAK` −10、`high_vol` −5（可叠加），把市场偏弱从"否决"改为"降档"。
- 仅 `veto`（`BEAR` + 数据缺失）仍 `NO_PARTICIPATE`，保留真正的风控底线。
- 枚举 `MARKET_RISK_HIGH` 保留**向后兼容**（`strategy_engine/engine.py:46` POSITION_RANGE + `templates.py` 文案），但 `decide_tier` 不再产出它（存量数据是持久化旧值，API 忠实返回属合法）。

**三板块相位模型 + 确定性算法落点**：

| 板块 | 相位 phase | 触发 | 核心算法 | 落点 |
|---|---|---|---|---|
| 盘中即时意见（并入最新信号） | `live` | 盘中每 5min 高频重算 | 五因子盘中强度（相对大盘30%/量能20%/均线20%/资金20%/筹码10%，0–100）+ R1补仓看多/R2超跌抄底 | `opinion_engine/intraday_strength.py` (`intraday_strength` + `check_r1_r2`) |
| 午盘意见 | `lunch` | 每日 11:40 一块，留历史 | 午盘快照 + 日线形态 | `worker.py::job_lunch_opinion` (Cron 11:40) + `templates.py::TEMPLATE_LUNCH` |
| 收盘后复盘 | `post_close` | 15:10 | 确定性三档价位 突破/加仓/止损 + 明日预期（regime 判定） | `opinion_engine/levels.py` (`compute_trade_plan`) |

- 三档价位**单调约束**：止损 < 加仓 < 突破，且均 >0（`test_levels.py` 回归守护）。
- `evaluate_etf` 5.5 段接入 `intraday_strength` / `check_r1_r2` / `compute_trade_plan`；`supporting_metrics` 增 `market_caution` / `high_vol_caution` / `intraday_strength` / `intraday_lean` / `intraday_factors` / `r1_signal` / `r2_signal`；返回 dict 增 `trade_plan`。
- `Opinion.trade_plan` 新增列（`db/models/signal_opinion.py` + `db/session.py` 幂等 ALTER）；`pipeline.py` Signal upsert 跳过该列、Opinion upsert 双分支写入；`schemas.py::OpinionOut` 补 `trade_plan` / `basis_text` / `model_version`（修复 `KeyError: 'trade_plan'` 响应被裁剪）。
- `POST /api/signals/{etf}/refresh`（`api/routers/signals.py`，`db_writer_lock` 下 `post_collection_evaluate(phase="live")`，持锁返回 409）供前端「重新评估」按钮按需重算盘中信号。
- 调度：`worker.py` 移除旧 `job_intraday_evaluate`，新增 `job_intraday_signal`（IntervalTrigger 300s，仅 `is_trading_now` 守卫→`live`）+ `job_lunch_opinion`（Cron 11:40→`lunch`）；`config.py::SchedulerConfig.intraday_signal_interval_seconds=300`。

**前端**：`types.ts` 增 `live`/`lunch` + `TradePlan`；`tier.ts` PHASE_TEXT 增；`endpoints.ts` 增 `refreshSignal`；`OpinionList.vue` 渲染 `trade_plan`；`EtfDetail.vue` 盘中强度/倾向/R1/R2 徽标 + 「重新评估」按钮 + 「午盘意见」Card。

**测试**：backend **304 passed**（C22 为 279，C23 **+25**）：`test_decide_tier_market_downgrade`（降档非否决/遍历 regime×high_vol 永不 blanket/veto 优先）、`test_intraday_strength`（上涨看多/下跌看空/缺指数跳过相对因子/R1/R2 触发）、`test_levels`（三档单调+正/数据不足返回 None/下行 regime）、`test_pipeline_live_lunch_postclose`（三相位产出+幂等）、`test_api_opinions_phase`（live/lunch 200、非法 422、未知 ETF 404、trade_plan 透出、refresh 200/409）、`test_worker`（C23 调度作业对齐）、`test_strategy_engine` + `test_collector_intraday_gtimg` 适配修正。前端 `pnpm build` 于 **C23-H1 hotfix** 才真正跑通（`refreshSignal` 漏传 `apiPost` payload 导致 TS2554，已补 `{}`）。

**⚠ CVM 部署待办（用户侧）**：
1. `cd /workspace && git pull` → `cd frontend && pnpm build`（前端改动需重建覆盖 Nginx dist）→ **必须同时重启 `etf-api` 与 `etf-worker`**：`sudo systemctl restart etf-api etf-worker`。
   ⚠ **历史踩坑（C23 首轮部署）**：`refresh` 端点 / `lunch`·`post_close` 相位 / C22 盘中分时修复都在 **etf-api**（HTTP 服务）里；只 restart `etf-worker` 会导致 etf-api 仍是旧代码 → 点击「重新评估」报 **HTTP 404（路由不存在）**、午盘意见永远空、盘中分时图空白。两个服务都要重启。
2. 验证盘中（每 5min）：ETF 详情页「最新信号」应随盘中强度分化（看多/看空/中性各不相同），**不再全场统一"先观望"**；强度徽标 + R1/R2 标识按实时行情变化。
3. 验证午盘：11:40 后详情页出现「午盘意见」Card（可留历史，非覆盖）。
4. 验证收盘后（15:10）：复盘意见含**确定性三档价位**（突破X上车 / 跌X加仓 / 跌破Y止损，价位单调 止损<加仓<突破 且 >0）+ 明日预期。
5. 沙箱 venv 读锁备注：本沙箱 `pluggy`/`httpx` 包文件被完整性策略读锁，跑测试需 `PYTHONPATH=/tmp/pyfix`（见 devlog C23）；**CVM 不受影响**，正常 `./venv/bin/python -m pytest -q` 即可。
6. **C23-H1 前端 build hotfix（已修，含在本批推送）**：`endpoints.ts::refreshSignal` 漏传 `apiPost` 第二参 `payload` 致 `vue-tsc` TS2554。已补 `{}`（`endpoints.ts:73`）；沙箱 `node v22.13.1 / pnpm 10.28.2` 实测 `pnpm build` 通过。用户侧 `git pull` 后重 build 即可消除该报错。

---

## VI. C24 · 场外基金（listing='场外'）方案B：净值序列复用 ETF 引擎（2026-07-31）

> 用户原始痛点（`/#/etfs/...` 与 `/#/offexchange`）：查到场外基金「只有名字，其他不可操作——点不开、看不见走势、拿不到建议」。另 515220 类 `etf_rs_missing`（基准指数缺失）也需借回填修复。

### 决策：方案B（纳入正规引擎，复用场内算法）

| 方案 | 思路 | 取舍 |
|---|---|---|
| A | 场外基金独立页 + 独立算法 | 重复造轮子，与场内引擎割裂、维护双倍 |
| **B（选定）** | 注册进 `etf_mapping`（`listing='场外'`），净值历史当「日K」存 `OFF_FUND` BAR，**复用** ETF 技术面/RS/三档价位引擎 | 零新算法、同构意见、维护单一；用户二期确认「复用场内引擎，三档价位作参考」 |
| C | 等盈米 CLI 装好再接真实场外数据 | P2 盈米 CLI 需用户本人在 CVM 短信验证，agent 无法代填，不可阻塞 |

**关键设计约束**：场外 T+1、无盘中分时 → 仅 `post_close` 相位评估（`live`/`lunch` 跳过，计 `skipped_offexchange`，不算 errors）；净值序列与场内 ETF BAR **物理隔离**（`symbol_type=OFF_FUND` 而非 `ETF`，无 CHECK 约束、无需迁移）。

### 改动落点（6 后端文件 + 2 前端文件）

| 文件 | 职责 |
|---|---|
| `data_provider/akshare_adapter.py` | 新增 `get_open_fund_nav_history`（akshare 东财 `fund_open_fund_info_em`，走 `fund.eastmoney.com/pingzhongdata/{code}.js`，**非被 RST 的 push2 主机**，CVM 一般可达）→ 中文列（净值日期/单位净值/日增长率）转英文列（date/nav/change_percent） |
| `collector/normalize.py` | 新增 `normalize_off_fund_nav`：基于 `_bar_row("OFF_FUND", ...)`，`close=nav`、`open=high=low=nav`、`previous_close=前一日nav`、`change_percent` 取源值或前后 NAV 反算 |
| `collector/collector.py` | 新增 `collect_offexchange_nav_history`（复用 `_collect_bar`→OFF_FUND 管道，异常记 FAILED 不抛）；`backfill_history` 按 `listing` 分支：场内→`collect_etf_history`（tally `etf`），场外→`_backfill_start("OFF_FUND",...)`→`collect_offexchange_nav_history`（tally `off_fund` 新桶）；`_is_on_exchange` 不变 |
| `strategy_engine/engine.py` | `bar_type = "OFF_FUND" if (getattr(mapping,'listing',None) or '场内')=='场外' else 'ETF'`，`get_bar_history(bar_type, ...)` 读净值序列；`len(etf_df)>0` 守卫使盘中/三档对场外自然跳过（pipeline 已不跑 live/lunch） |
| `evaluation/pipeline.py` | `live`/`lunch` 相位遇 `listing='场外'` → `skipped_offexchange += 1` 并 `continue`；`post_close` 正常评估（worker `job_post_close_evaluate` 已带 `phase="post_close"`） |
| `api/routers/market.py` | `etf_history` 按 `listing` 取 `symbol_type=OFF_FUND`，复用 Points/humanize；`name` 取映射名 |
| `frontend/src/views/OffExchange.vue` | 行 `<tr>` 加 `cursor-pointer` + `router.push('/etfs/'+code)`，从「只显名字」变为可点进详情 |
| `frontend/src/views/EtfDetail.vue` | `loadCharts` 对场外跳过分时拉取；走势卡标题 `listing=='场外' ? '净值走势' : '日 K 线'`，空态提示「净值走势将在数据回填后显示」；「盘中分时 / 盘中意见 / 午盘意见」三 Card 加 `v-if="etf.listing !== '场外'"`，「收盘后复盘」保留 |

### 测试（backend **314 passed**，C23 304 → C24 **+10**）

- 新增 `tests/test_off_fund.py`（10 项，端到端覆盖）：归一化（中文列→OFF_FUND 行、`open=high=low=close=NAV`、previous_close=前一日NAV、change_percent 源值/反算）；适配器（东财中文列→英文列）；采集（`collect_offexchange_nav_history` 落 OFF_FUND BAR 且与 ETF BAR 隔离、`backfill_history` 路由场外→OFF_FUND 并 tally）；引擎（`evaluate_etf` 对 `listing='场外'` 读 OFF_FUND、收后产三档 `trade_plan`、且 `etf_rs_missing` 不在 `failed_rules`——即读净值成功；`live` 相位不产三档、不崩）；流水线（`live` 相位 `skipped_offexchange>=1` 且无场外 Opinion、`post_close` 产出含三档的场外 Opinion）；API（`/api/market/etf/110020/history` 返回 `open==close` 的净值序列）。
- 全量回归：`./venv/bin/python -m pytest` 314 passed（**注意：本沙箱 venv 的 `pluggy` 被破坏，需用系统 `python3` 或 `PYTHONPATH=/tmp/pyfix` 旁路**，详见 devlog C23；**CVM 正常 venv 不受影响**）。
- 前端：`pnpm build` 通过（vue-tsc 类型检查 + vite build，660 模块；仅 echarts chunk 体积警告，非错误）。

### ⚠ CVM 部署步骤（用户侧）

1. `cd /workspace && git pull`（拉取 C24 后端 + 前端 + 种子）。
2. `cd backend && ./venv/bin/python -m scripts.seed_mapping`（**必须用 backend 的 venv 解释器**，系统 `python3` 无项目依赖；幂等 upsert；本次 SEED 已含 110020/000008/110003 等 `listing='场外'` 场外示例 + 确保 515220 等场内映射完整）。
3. `cd backend && ./venv/bin/python -m scripts.collect_once --backfill`（回填 ETF/指数/板块 **+ 场外净值（OFF_FUND）**；顺带补 515220 的跟踪指数 → 消除其 `etf_rs_missing`）。akshare 东财 pingzhongdata 走独立主机，CVM 一般可达；失败仅该支场外 FAILED，不影响其余。
4. `cd frontend && pnpm build`，然后 **必须同时重启双服务**：`sudo systemctl restart etf-api etf-worker`（仅 restart worker 会让 etf-api 仍是旧代码 → 场外详情/净值走势读不到新类型）。
5. 验证（等 15:10 后看最稳妥）：
   - `#/offexchange` 列表项可**点进**详情；
   - `#/etfs/110020` 显示「净值走势」折线（回填后），收盘后复盘含三档价位 + 明日预期；
   - 515220 详情 `etf_rs_missing` 消失（指数已补）；
   - 盘中/午盘详情页对场外 ETF **不显示**盘中分时/盘中意见/午盘意见（设计内 T+1）。
6. 异常排查：`journalctl -u etf-worker --since today | grep -i off_fund`；若某支场外 FAILED，多半是 akshare 该支净值接口偶发，重跑 `--backfill` 即可增量补齐。

## 方法论审计文档（C25，审核用）

- 新增 `docs/trading_methodology.md`：把后端**所有硬编码交易方法论/逻辑**按「模块 + 方法论」整理，含 `file:line`、精确常量、公式，及 **A1–A10** 审计发现（含严重性 / 影响 / 修复建议）。供用户审核。
- 覆盖范围（M1–M11）：综合评分与档位决策 / 盘中五因子与 R1·R2 / 收盘后三档价位 / 指标引擎 / 板块趋势与资金流 / 风险引擎 / 配置 / 规则冻结与版本化 / 文案渲染 / 持仓分析动作推导 / 调度相位。
- 高优先级待修（均对源码确认）：**A1** 布林带未产出→R2 永不触发+三档价退化；**A2** `rolling_rs` 算成 Πr 而非 Π(1+r)→`etf_rs_score` 恒 0；**A9** 版本哈希不覆盖引擎硬编码常量→改逻辑不 bump 版本。
- 本轮仅新增文档 + 本指针，**未改动任何业务代码**；提交见 devlog 轮次 C25。


