# A股板块资金监控与 ETF 辅助分析工具 — 系统设计文档（V5，含 P-1 / P-1b 验证结论）

> 状态：设计阶段；**P-1（基础行情）与 P-1b（策略历史数据闭环）均已完成验证**；P0/P1 可立即开始，P-1b 已冻结策略数据依赖
> 作者视角：资深 Python 后端 / 量化架构 / 前端
> 目标读者：项目 owner + 后续开发（≤10 人内部使用，4 核 4GB 内存 / 60GB 系统盘）

---

## 0. 产品定位与已确认关键决策

### 产品定位

> **第一阶段是公共市场异动与 ETF 机会监控系统，不做完整账号和个人资产管理；持仓建议采用用户主动提交、服务端即时计算且默认不保存的无状态接口。等确实需要保存个人持仓和历史建议时，再增加轻量用户系统。**

### 已确认的关键决策（V5）

| 决策点 | 结论 |
|---|---|
| 数据源 | AkShare 免费接口；**多源可插拔**：生产优先东方财富(em)，沙箱/降级用新浪(sina)+同花顺(ths)；适配器隔离（见 §3.1） |
| 数据源可用性 | **东方财富(em)在开发沙箱被防火墙拦截**；新浪/同花顺/腾讯可达。缺失字段（大单净流入/涨跌家数/涨跌停数）仅 em 提供，生产服务器（国内网络）应可补齐 |
| 策略历史闭环 | **P-1b 已验证**：指数历史(tx 可达)；板块历史/板块历史资金流仅 em（生产验证）；历史涨跌家数无 API，须每日从全市场快照累计（见 §3.1） |
| 数据源切换一致性 | 资金持续性**仅同数据源同口径**计算；切源重积累窗口并降置信度。新增 `metric_source`/`metric_definition_version`/`source_switched` 字段 |
| 时间语义 | `source_timestamp`（数据源时间，可空）与 `collected_at`（采集时间）分离；源无时间则 `source_timestamp=NULL, timestamp=collected_at` |
| 回测异步 | `POST /api/backtest/run` 仅建 PENDING 任务返回 id；Worker 收盘后执行；盘中禁重型回测，避免与采集竞争 |
| DB 索引 | 在唯一键外新增 4 个核心索引（见 §5.6），防一年数据后查询变慢 |
| Nginx 端口隔离 | Uvicorn 仅监听 127.0.0.1；防火墙封 FastAPI 端口；公网仅 80/443；强制 HTTPS，防绕过 Nginx 直连 |
| 刷新频率 | 默认 3 分钟（收盘前可提频） |
| MVP 是否接 LLM | 模板化生成，暂不接 LLM |
| ETF 范围 | 精选 30–50 只主流 ETF，手动映射 |
| 访问保护 | **MVP：仅 Nginx Basic Auth + HTTPS**；FastAPI 不实现鉴权层；脚本用单独 API Token 留到第二阶段 |
| 实时推送 | **MVP：REST + 前端轮询（每 30 秒）**，不做 SSE；第二阶段确需即时通知再加 |
| 持仓分析 | 无状态按需接口，默认不保存请求数据 |
| 评分维度 | 5 类（风险为否决/降级条件，不重复扣分） |
| 前端页面 | 5 个（总览/板块/ETF列表/ETF详情/系统状态） |
| 服务进程 | `etf-api.service`（1 worker）+ `etf-worker.service`（采集/计算/信号，单实例） |
| 数据源故障 | 生产不降级 Mock；沿用上一份真实数据 + STALE + 暂停新意见 + 前端展示延迟 |
| 行情区分 | **`data_kind`（SNAPSHOT/BAR）+ `timeframe`（snapshot/1m/3m/5m/1d）**，轮询快照 ≠ K线 |
| 策略版本 | **不可覆盖**：`strategy_hash = SHA256(规则+参数)`，内容变则生成新版本（如 v1.0.0-a83f29） |
| 回测表 | **P1 建 8 张核心表；P7 新增 `backtest_run` / `backtest_trade` 两张** |
| 条件触发 | **改名为 `post_collection_evaluate`**：采集→算指标→查阈值→出信号，非实时事件 |
| 仓位表达 | 第一版前端以文字为主（不新增/轻仓试错/维持低仓位/逐步降低风险敞口），内部保留数值 |
| 进程数 | **API 1 worker、采集 1 进程**；4GB 更轻松，避免进程内缓存不一致 |
| 备份 | `sqlite3 .backup`（非 cp）；本地日备 7 天 + 周传异地/对象存储；日志 14–30 天；旧快照 1–2 年归档 |

### 未确认但已做默认假设的清单（请 owner 复核）

1. **时间存储**：数据库时间字段**统一存 UTC**；`trading_date` 按北京时间判定；前端拉到后转换为北京时间展示。
2. **复权**：ETF 历史与指标默认前复权用于回测；盘中展示用不复权实时价。配置可切换。
3. AkShare 为同步库，采集在 worker 线程池执行，不阻塞 API。
4. SQLite 启用 WAL + 单写者；worker 单实例保证写入不冲突。
5. 前端本地/CI 构建静态文件，Nginx 托管；FastAPI 只跑 REST。
6. 节假日/交易日历用 `ak.tool_trade_date_hist_sina()` 或本地 YAML 交易日列表。
7. "主力资金流入"视为数据源计算结果，只做持续性/排名，标注 `data_source`。
8. 回测基准默认沪深 300 ETF（510300），可在策略配置改。
9. 策略白名单参数 MVP 经 YAML 配置 + 启动时注册不可覆盖的 `strategy_version`。
10. Mock 数据源仅允许 `DATA_SOURCE=mock` 的开发/测试环境。
11. **环境**：开发沙箱系统 Python 3.12 为 externally-managed，实际用 pyenv `python3.11.1`（已带 pandas 3.0 等）；部署按 DESIGN 用 3.12 + venv。沙箱网络非中国大陆，故 em 不可达——这是沙箱现象，非设计缺陷。

---

## 1. 需求中的不确定点与风险

| # | 不确定点 / 风险 | 缓解措施 |
|---|---|---|
| R1 | AkShare 接口变动、限频、盘中超时 | 适配器隔离 + data_quality + 重试退避 + `data_source_status` 告警 |
| R2 | 3 分钟频率错过秒级急拉急跌 | 收盘前提频；意见中标注"数据频率限制" |
| R3 | "主力净流入"口径不透明 | 只做持续性/排名，跨源校验前不绝对化，标注来源 |
| R4 | 未来数据泄漏 | 回测严格用"信号下一可交易时刻"成交价；指标用截至信号时刻窗口 |
| R5 | 参数过拟合 | 样本内/外分离 + 白名单参数范围约束 + 多阶段表现对比 |
| R6 | 4 核 4GB 内存紧张 | 前端静态化；API(1 worker)/Worker(1) 分离；大查询分页；回测分批读 |
| R7 | 板块/ETF 时间戳不同步 | 按 `trading_date + 最近快照` 对齐，缺失取上一可用快照并标 `stale` |
| R8 | 复权/不复权混用 | 回测强制前复权；实时展示不复权；配置显式声明 |
| R9 | 涨跌停/停牌无法成交 | 回测"涨停不买、跌停不卖、停牌跳过"约束 |
| R10 | 市场日历误差 | market_calendar 单点判断，所有任务先查日历再执行 |
| R11 | 策略参数被改导致不可复现 | **`strategy_hash` 不可覆盖版本**；信号/意见存版本号 |
| R12 | 生产数据源故障 | 不降级 Mock：沿用上一份真实数据 + STALE + 暂停新意见 + 前端告警 |
| R13 | 无用户系统下的访问控制 | **Nginx Basic Auth + HTTPS**；内网/白名单 IP 可选；脚本 Token 留第二阶段 |
| R14 | 轮询快照误当 K线 | **`data_kind` 显式区分 SNAPSHOT/BAR**，指标/回测只用 BAR |
| R15 | WAL 下直接 `cp` 备份损坏 | **用 `sqlite3 .backup`**；异地周备防磁盘损坏 |

---

## 2. MVP 范围（第一阶段交付）

**纳入 MVP：**
- 项目骨架 + YAML/env 配置 + 日志轮转（两入口 main/worker）
- SQLite 数据模型（P1 八张核心表；P7 加两张回测表）
- AkShare 适配器 + Mock 适配器（Mock 仅 dev/test）
- 盘中定时采集（3 分钟）+ 数据质量检测 + 数据源状态
- 指标引擎（均线/动量/RSI/MACD/量能/波动率）
- 板块强弱 + 资金持续性评分
- 策略引擎（5 类评分 → ETF 公共信号）+ 风险否决/降级 + **不可覆盖版本**
- 模板化意见生成（盘中 + 收盘复盘）
- 按需持仓分析接口（无状态，默认不保存，含输入校验）
- 日线回测引擎（含手续费/滑点/涨跌停/停牌/样本内外）
- FastAPI（REST，**无鉴权层**，由 Nginx Basic Auth 保护；**不做 SSE**）
- Vue 前端 **5 个页面**（前端每 30 秒轮询 `overview`/`signals/latest`）
- Nginx（Basic Auth + 静态托管 + 反代）+ Uvicorn（etf-api 1 worker / etf-worker 单实例）+ 备份脚本
- pytest 基础测试 + 运维文档

**暂缓（第二阶段及以后）：**
- 完整用户系统（JWT/角色/用户表/登录页/权限/审计）
- SSE / WebSocket 实时推送
- 脚本调用用 API Token（网页仍走 Basic Auth）
- 策略可视化配置页、参数修改历史 UI
- 复杂回测页、个人持仓长期保存、用户建议历史
- 真实 LLM 润色（架构预留 `model_version`/`input_summary`）
- 双数据源实时校验（适配器已支持，MVP 只接 AkShare）

---

## 3. 系统架构图

```
                         Nginx（Basic Auth + HTTPS）
                        ├── Vue 静态页面（前端每 30s 轮询）
                        └── /api/* 反向代理（无 FastAPI 鉴权层）
                                │
            ┌───────────────────┴────────────────────┐
            │                                         │
   etf-api.service（1 worker）              etf-worker.service（单实例）
   ┌──────────────────────────┐          ┌────────────────────────────┐
   │ FastAPI                   │          │ APScheduler                 │
   │  REST 查询 / 持仓分析      │          │  AkShare 采集              │
   │  /portfolio/analyze        │          │  指标计算                  │
   │  /backtest/*               │          │  post_collection_evaluate  │
   └────────────┬─────────────┘          │  市场信号 + 意见生成         │
                │                        └──────────────┬─────────────┘
                │ 读取                                    │ 写入
                └─────────────────┬──────────────────────┘
                                  ▼
                          SQLite（WAL 模式，UTC 存储）
                  行情 / 信号 / 意见 / 任务 / 数据源状态 /（P7）回测

  前端轮询节奏：每 30s 请求 GET /api/market/overview + GET /api/signals/latest
  Worker 生成新信号 → 写 SQLite → 前端下一次轮询即可看到（非实时推送）
```

**资源占用（4 核 4GB / 60GB）**：Ubuntu ~0.5–1GB；FastAPI(1 worker) ~0.1–0.3GB；Worker+Pandas ~0.3–1GB；Nginx 数十 MB；SQLite 几乎不常驻。控制点：不一次加载多年全量；回测按标的+时间窗分批读；采集/API 分进程；不保存逐笔成交；日志/备份定期清理。

### 3.1 数据接入层（data_provider）与 P-1 验证结论

`data_provider` 是业务代码与具体网站接口之间的唯一隔离层。P-1 已用真实网络验证 AkShare 可用函数与字段，结论如下。

**适配器接口（BaseDataProvider 抽象方法）**
| 能力 | 方法 | 说明 |
|---|---|---|
| 交易日历 | `get_trade_calendar()` | 返回交易日列表 |
| 指数实时 | `get_index_snapshot()` | 宽基/主要指数 SNAPSHOT |
| 板块排行+资金流 | `get_sector_ranking(type)` | INDUSTRY / CONCEPT，含涨跌幅+净额+领涨股 |
| ETF 实时 | `get_etf_snapshot()` | 全部 ETF SNAPSHOT |
| ETF 历史日线 | `get_etf_history(symbol, start, end)` | BAR（前复权可配） |
| 指数历史日线 | `get_index_history(symbol, start, end)` | BAR（MA20 / 成交额5日均值） |
| 板块历史日线 | `get_sector_history(symbol, start, end)` | 板块 MA20 / RSI / 5·10·20 日动量 |
| 板块历史资金流 | `get_sector_fund_flow_history(symbol, start, end)` | 连续 N 日净流入 |
| 历史市场宽度 | `get_market_breadth_history(start, end)` | 上涨占比/涨跌停（无 API→每日累计） |

**已验证的真实函数名（AkShare 当前版本，P-1 + P-1b）**
| 能力 | 首选（生产，em） | 沙箱可用（sina/ths/tx） | 验证结果 |
|---|---|---|---|
| 交易日历 | `tool_trade_date_hist_sina` | 同左（sina） | ✅ 8797 行 |
| 指数实时 | `stock_zh_index_spot_em` | `stock_zh_index_spot_sina` | ✅ 562 行（sina） |
| 行业板块 | `stock_board_industry_name_em` | `stock_fund_flow_industry`（ths，含资金流） | ✅ 90 行（ths） |
| 概念板块 | `stock_board_concept_name_em` | `stock_fund_flow_concept`（ths） | ✅ 386 行（ths） |
| ETF 实时 | `fund_etf_spot_em` | `fund_etf_category_sina` | ✅ 1602 行（sina） |
| ETF 历史 | `fund_etf_hist_em` | `fund_etf_hist_sina` | ✅ 3436 行（sina, sh510300） |
| 指数历史 | `stock_zh_index_daily_em`(含amount) | `stock_zh_index_daily_tx`(含amount) / `stock_zh_index_daily`(无amount) | ✅ tx 8684 行（含amount） |
| 板块历史 | `stock_board_industry_hist_em` | 无（sina/ths 无日线历史） | ⚠️ em-only，生产验证 |
| 板块历史资金流 | `stock_sector_fund_flow_hist` | 无直接历史；上线后每日积累 | ⚠️ em-only，生产验证 |
| 历史市场宽度 | 每日 `stock_zh_a_spot_em` 累计 | `stock_zh_a_spot`(sina,可达) 每日累计 | ✅ 累计源可达；无历史 API |

> ⚠️ **函数名坑**：① 板块资金流**不是** `stock_board_industry_fund_flow_em`（不存在），正确是 `stock_fund_flow_industry` / `stock_fund_flow_concept`。② 指数历史**不是** `stock_zh_index_daily_sina`（不存在），正确是 `stock_zh_index_daily`（sina, 无 amount）或 `stock_zh_index_daily_tx`（腾讯, 含 amount）。

> ⚠️ **历史涨跌家数无直接 API**：必须每日用 `stock_zh_a_spot` 拉全市场快照→计算上涨/下跌/平/涨跌停家数→写入 `market_breadth` 累计。上线前 breadth 相关规则（上涨占比/涨跌停）无历史可回测，前端标记"观察期数据不足"；**禁止用单日资金流伪造"连续3日净流入"**。

**多源降级策略（适配器内实现，P2 落地）**
1. 配置 `DATA_SOURCE_PREFERRED: em`；`FALLBACK: [sina, ths]`。
2. 每次采集按首选→降级顺序尝试，首个成功即返回；全部失败记 `data_source_status` 连续失败。
3. 统一经 `normalize` 层映射到 `market_quote` 模型（见 §5），业务代码不感知来源。

**统一模型字段覆盖（P-1 实测）**
| 状态 | 字段 |
|---|---|
| 直接可用 | symbol / open / high / low / close / previous_close / volume / amount / change_percent / main_net_inflow（ths 净额） |
| 需派生 | data_source / symbol_type / data_kind / timeframe / trading_date / timestamp / collected_at / data_quality_status |
| **仅 em 提供（沙箱缺失）** | `large_order_inflow`（大单净流入）、`rise_count`/`fall_count`（板块涨跌家数）、`limit_up_count`/`limit_down_count`（涨跌停数） |

> 沙箱当前用 sina/ths，故 `large_order_inflow` 与板块涨跌家数/涨跌停数为空；这些字段在 em 可达的生产环境由 `stock_board_industry_name_em` + `stock_zt_pool_em` 补齐。`normalize` 层对缺失字段写 `NULL` 并由 `data_quality` 标记，策略引擎对缺失字段做降级而非报错。P-1 完整报告见 `backend/scripts/p1_output/report.json`。

---

## 4. 模块依赖关系（DAG）

```
config ──────────────► 所有模块

market_calendar ──► scheduler, collector
data_provider   ──► collector
repository      ──► collector, data_quality, indicator_engine,
                   sector_engine, strategy_engine, backtest_engine,
                   opinion_engine, api

collector       ──► repository, data_quality
data_quality    ──► repository
indicator_engine──► strategy_engine, sector_engine, backtest_engine
sector_engine   ──► strategy_engine, opinion_engine
etf_mapping     ──► strategy_engine, opinion_engine, api
strategy_engine ──► risk_engine(否决/降级), opinion_engine, api
risk_engine     ──► opinion_engine, api（否决/降级，非重复扣分）
backtest_engine ──► repository, indicator_engine, strategy_engine, risk_engine
opinion_engine  ──► api
portfolio       ──► api, repository（读最新 signal/indicator，无写入）
scheduler(worker)──► collector, opinion_engine, (按需) backtest_engine
api             ──► repository, opinion_engine, strategy_engine, risk_engine,
                   sector_engine, etf_mapping, portfolio, system(status)
```

**原则**：采集/指标/策略/文字生成严禁写在同一函数；模块间只通过统一数据结构通信；业务代码不直接 import AkShare；**FastAPI 无鉴权层，鉴权全在 Nginx**。

---

## 5. 数据库表设计（SQLite，WAL，UTC 存储）

> **时间统一存 UTC**；`trading_date` 按北京时间判定；前端转北京时间展示。
> **行情唯一键**：`data_source + symbol_type + symbol + data_kind + timeframe + timestamp`。
> `data_kind` ∈ {SNAPSHOT, BAR}；`timeframe` ∈ {snapshot, 1m, 3m, 5m, 1d}。
> 轮询抓到的"当时开高低现价"= **SNAPSHOT**；某周期的独立 OHLC = **BAR**（如 3m）。

### 5.1 行情类

**`market_quote`（统一行情）**
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| data_source | TEXT | 来源标识 |
| symbol_type | TEXT | INDEX/INDUSTRY/CONCEPT/ETF |
| symbol | TEXT | 代码 |
| data_kind | TEXT | SNAPSHOT / BAR |
| timeframe | TEXT | snapshot / 1m / 3m / 5m / 1d |
| trading_date | DATE | 按北京时间判定的交易日 |
| timestamp | DATETIME | **UTC** 快照时间 |
| open/high/low/close | REAL | OHLC |
| previous_close | REAL | 昨收 |
| volume / amount | REAL | 成交量(手)/成交额(元) |
| change_percent | REAL | 涨跌幅 % |
| turnover_rate | REAL | 换手率 % |
| main_net_inflow | REAL | 主力净流入（数据源计算值） |
| large_order_inflow | REAL | 大单净流入 |
| rise_count / fall_count | **INTEGER** | 板块内涨跌家数 |
| limit_up_count / limit_down_count | **INTEGER** | 涨停/跌停家数 |
| collected_at | DATETIME | **UTC** 实际采集时刻 |
| source_timestamp | DATETIME | **UTC** 数据源返回的行情时间；源无则 NULL（此时 timestamp = collected_at） |
| metric_source | TEXT | 该条指标/资金流的数据源标识（em/sina/ths），用于同源性校验 |
| metric_definition_version | TEXT | 资金流等指标口径版本，变更需新版本 |
| source_switched | INTEGER | 本条相比上一条是否发生数据源切换（0/1） |
| data_quality_status | TEXT | OK/STALE/MISSING/DELAY/ANOMALY |
| unique(data_source, symbol_type, symbol, data_kind, timeframe, timestamp) | | 幂等写入 |

**`market_breadth`（全市场宽度/情绪）**
| 字段 | 说明 |
|---|---|
| id, trading_date, timestamp(UTC), total_rise(INT), total_fall(INT), total_flat(INT), limit_up(INT), limit_down(INT), total_amount, data_source, collected_at(UTC), data_quality_status | |

### 5.2 映射与策略

**`etf_mapping`**：id, etf_code, etf_name, related_sector_codes(JSON), related_index_code, category, is_active, mapping_version, valid_from, valid_to, notes, created_at, updated_at
> 映射修改生成新 `mapping_version`，旧映射**不覆盖**；回测按 `valid_from`/`valid_to` 取当时生效映射，避免用今天映射回测历史。

**`strategy_version`**：version(PK, 如 v1.0.0-a83f29), strategy_hash(TEXT, 唯一), name, description, params_json, rules_json, created_at
> **不可覆盖**：`strategy_hash = SHA256(规则JSON + 参数JSON)`；内容变化必须生成新版本；插入前查重，旧版本禁止改写。

### 5.3 信号与意见

**`signal`**
| 字段 | 类型 | 说明 |
|---|---|---|
| signal_id | TEXT(PK) | uuid |
| strategy_version | TEXT | 关联不可覆盖版本 |
| generated_at(UTC) / trading_date | | |
| target_etf | TEXT | |
| signal_type | TEXT | 公共建议档位（见 9.4） |
| score | REAL | 综合分 |
| confidence | REAL | 置信度 0–1 |
| market_regime | TEXT | STRONG_UP/TREND_UP/VOLATILE/WEAK/BEAR |
| triggered_rules / failed_rules | JSON | 命中/未满足规则 |
| supporting_metrics | JSON | 支撑指标值 |
| risk_flags | JSON | 风险标记（否决/降级依据） |
| invalidation_conditions | JSON | 失效条件 |
| suggested_action | TEXT | 建议动作（前端文字化） |
| suggested_position_range | JSON | 内部数值区间 %（前端先不精确展示） |
| review_time(UTC) | DATETIME | 下次复核时间 |

**`opinion`**：opinion_id(PK), signal_id(NULL), generated_at(UTC), trading_date, phase, title, content, input_summary(JSON), template_version, model_version(预留 "template-v1")

### 5.4 系统与运维

**`task_run_log`**：id, task_name, trigger_type, started_at(UTC), finished_at(UTC), status(SUCCESS/FAILED/TIMEOUT/SKIPPED), items_processed, error_message, data_delay_seconds
**`data_source_status`**：id, data_source, symbol_type, last_success_at(UTC), last_attempt_at(UTC), consecutive_failures, status, note

### 5.5 回测表（**P7 新增，非 P1 八张核心表**）

**`backtest_run`**：id, strategy_version, status(PENDING/RUNNING/DONE/FAILED), progress(0-100), start_date, end_date, params_json, benchmark, results_json(指标), trades_count, created_at(UTC), created_by, finished_at(UTC)
**`backtest_trade`**：id, backtest_run_id, etf_code, entry_time(UTC), exit_time(UTC), entry_price, exit_price, qty, pnl, pnl_percent, reason

### 5.6 核心索引（除唯一键外必加，防一年数据后查询变慢）
```sql
CREATE INDEX idx_quote_symbol_time ON market_quote(symbol, data_kind, timeframe, timestamp);
CREATE INDEX idx_quote_trade_type ON market_quote(trading_date, symbol_type);
CREATE INDEX idx_signal_etf_time   ON signal(target_etf, generated_at);
CREATE INDEX idx_task_name_time    ON task_run_log(task_name, started_at);
```

> P1 核心 8 张：market_quote / market_breadth / etf_mapping / strategy_version / signal / opinion / task_run_log / data_source_status。
> 暂缓（第二阶段）：`user`、`login_log`、`strategy_param_change_log`、个人持仓表、用户建议历史表。

---

## 6. 项目目录结构

```
/workspace
├── README.md / DESIGN.md
├── config/
│   ├── settings.yaml            # 频率/阶段/阈值/白名单参数/数据源开关
│   └── .env.example             # 密钥/Token 不入库
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口（etf-api，1 worker，无鉴权层）
│   │   ├── worker.py            # APScheduler 入口（etf-worker）
│   │   ├── config.py            # YAML+env（Pydantic Settings）
│   │   ├── db/{base,session,models/}  # ORM（P1 八张 + P7 两张）
│   │   ├── schemas/             # Pydantic 统一结构 + API 入出参
│   │   ├── data_provider/{base,akshare_adapter,mock_adapter}
│   │   ├── collector/{collector,normalize}
│   │   ├── market_calendar/calendar
│   │   ├── data_quality/checker
│   │   ├── indicator_engine/indicators
│   │   ├── sector_engine/sector
│   │   ├── etf_mapping/mapping
│   │   ├── strategy_engine/{rules,engine,versioning}  # versioning: SHA256 不可覆盖
│   │   ├── risk_engine/risk
│   │   ├── backtest_engine/backtester
│   │   ├── opinion_engine/{templates,generator}
│   │   ├── portfolio/analyzer    # 无状态，含输入校验
│   │   ├── scheduler/{jobs,triggers}
│   │   ├── repository/*.py
│   │   ├── api/routers/{market,sectors,etfs,signals,opinions,backtest,portfolio,system}
│   │   └── logging_conf.py
│   ├── tests/
│   ├── scripts/{init_db,seed_mapping,db_backup}
│   └── requirements.txt / pyproject.toml
├── frontend/
│   ├── index.html / vite.config.ts / package.json
│   └── src/
│       ├── main.ts / App.vue / router / stores / api(polling 30s) / components(ECharts)
│       └── views/               # 5 个页面
│           ├── MarketOverview.vue
│           ├── SectorRanking.vue
│           ├── EtfList.vue
│           ├── EtfDetail.vue     # 内嵌持仓分析表单/弹窗
│           └── SystemStatus.vue
├── deploy/
│   ├── nginx.conf               # Basic Auth + HTTPS + 静态托管 + 反代（无 FastAPI 鉴权）
│   ├── etf-api.service          # systemd（1 worker）
│   └── etf-worker.service       # systemd（单实例）
└── docs/{ops.md, api.md}
```

---

## 7. API 清单（REST，由 Nginx Basic Auth 统一保护；MVP 无 SSE）

### 行情 / 市场
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/market/overview` | 主要指数 + 宽度 + 成交额 + 风险状态（**前端每 30s 轮询**） |
| GET | `/api/market/breadth/latest` | 最新涨跌家数/涨跌停 |
| GET | `/api/market/quotes?symbol=&type=&data_kind=&tf=` | 单标的近期序列（SNAPSHOT/BAR 可选） |

### 板块
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/sectors/ranking` | 板块涨跌/资金流/持续性/强度排行 |
| GET | `/api/sectors/{code}` | 板块详情 + 关联 ETF |

### ETF
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/etfs` | ETF 列表（含评分/信号摘要） |
| GET | `/api/etfs/{code}` | ETF 详情 |
| GET | `/api/etfs/{code}/history` | K线/BAR 历史 |
| GET | `/api/etfs/{code}/signals` | 历史信号 |

### 信号 / 意见
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/signals/latest` | 最新公共信号列表（**前端每 30s 轮询**） |
| GET | `/api/signals/history` | 历史信号 |
| GET | `/api/opinions/current` | 当前盘中意见 |
| GET | `/api/opinions/history` | 历史意见/复盘 |

### 按需持仓分析（无状态，默认不保存）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/portfolio/analyze` | 提交持仓即时计算，默认不落库 |

请求（含校验）：
```json
{
  "positions": [
    { "etf_code": "510300", "cost_price": 3.82, "position_percent": 30, "quantity": 10000 }
  ]
}
```
**输入校验（服务端强制）**：
- 最多 20 只 ETF；不允许重复 ETF
- `cost_price` > 0；单项 `position_percent` ∈ [0,100]
- 所有项 `position_percent` 合计 ≤ 100
- 仅允许 `etf_mapping` 白名单内的 ETF
- `quantity` 可选：有则算具体盈亏金额；无则只算收益率与风险

返回：
```json
{
  "items": [
    {
      "etf_code": "510300",
      "action": "HOLD",
      "reason": "市场环境正常，ETF相对强弱仍为正",
      "risk": "跌破短期趋势线后需要重新评估",
      "return_percent": 2.1,
      "pnl_amount": 210.0,
      "suggested_position_text": "维持低仓位",
      "suggested_position_range": [20, 30],
      "invalidation_conditions": ["..."],
      "review_time": "2025-...T14:30:00Z"
    }
  ]
}
```
约束：不保存持仓；不产生用户状态；不做高频自动分析；仅主动调用时计算。

### 回测（异步，Worker 执行）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/backtest/run` | 创建 PENDING 任务，立即返回 `backtest_run_id`；**盘中默认拒重型回测** |
| GET | `/api/backtest/{id}` | 查进度（status/progress）与结果（完成后返回指标+交易+净值曲线） |
| GET | `/api/backtest/runs` | 回测记录列表 |

### 系统
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/system/status` | 系统健康/最后更新 |
| GET | `/api/system/tasks` | 任务运行记录 |
| GET | `/api/system/datasources` | 数据源状态（含 STALE 标记） |

> MVP **无 SSE 端点**；所有"新信号/异动"由前端 30s 轮询 `overview` + `signals/latest` 获得。撤销原 JWT 登录端点与 SSE 推送端点。

---

## 8. 定时任务清单（APScheduler，全部在 `etf-worker` 单实例；先查 market_calendar）

| 任务 | 触发（可配置） | 动作 | 通用约束 |
|---|---|---|---|
| `pre_market_prepare` | 08:50 | 拉前日数据、预热缓存、生成开盘前意见 | 同日只跑一次 |
| `open_observe` | 09:30–09:45 每 3 分钟 | 观察期采集 | 跳过若已采 |
| `intraday_am` | 09:45–11:30 每 3 分钟 | 采集 + `post_collection_evaluate` | 超时 60s、失败重试 2 次 |
| `midday_summary` | 11:35 | 午间总结意见 | 同日只跑一次 |
| `intraday_pm` | 13:00–14:30 每 3 分钟 | 采集 + `post_collection_evaluate` | 同上 |
| `pre_close` | 14:30–15:00 每 1–3 分钟（可配） | 高频采集 + 收盘前判断 | 同上 |
| `post_close_review` | 15:10 | 收盘复盘 + 信号归档 | 同日只跑一次 |
| `run_backtest` | 收盘后(15:40) 或手动 | 取 PENDING 回测任务执行；**盘中禁重型回测**（避免与采集竞争 CPU/内存） | 超时控制 + 失败重试 |
| `post_collection_evaluate` | 每次采集后 | **采集→算指标→查异动阈值→生成信号**（非实时事件；前端下次轮询可见） | 去重 |
| `health_heartbeat` | 每 5 分钟 | 数据源状态/延迟检查 | — |
| `db_backup` | 每天 02:00 | `sqlite3 .backup` + 压缩 + 本地留 7 天 | 周传异地/对象存储 |

**全部满足**：幂等（按 `trading_date+task+phase` 去重）、超时控制、失败重试、错误日志、运行记录（`task_run_log`）、数据延迟告警、重复执行保护（单实例 + `task_run_log` 防重）。

**数据源故障（生产）**：真实数据失败 → ①沿用上一份真实数据；②标记 `STALE`；③暂停生成新操作意见；④前端明确展示数据延迟。Mock 仅 `DATA_SOURCE=mock` 的开发/测试环境。

---

## 9. 第一版策略规则草案（确定性规则引擎，5 类评分）

> 评分区间 0–100。综合分 = 加权求和（权重在白名单参数，受 min/max 约束）。**风险不重复扣分，作否决/降级条件。**

### 9.1 五类评分
1. **市场环境 `market_score`**：宽基站上 MA20 且上行；全市场上涨占比 >60% 加分、<40% 减分；成交额较 5 日均放大加分；输出 `market_regime`。
2. **板块趋势 `sector_trend_score`**：收盘价 > MA20 且 MA20 上行；近 5/10/20 日动量分位；RSI(14) 50–70 健康，>80 过热（交风险过滤）。
3. **资金持续性 `fund_flow_score`**：主力净流入连续天数（默认 ≥3 日为正续）加分；净流入强度 = 净流入/板块成交额跨期比较；大单同向确认加分、背离减分（仅持续性/排名）。
4. **ETF 相对强弱 `etf_rs_score`**：ETF 相对关联板块/指数滚动 20 日 RS；同类排名分位。
5. **数据和追高风险过滤 `risk_filter`**（布尔/档位，非扣分）：RSI>80 或板块短期涨幅过大→追高；大盘 BEAR/高波动；ETF 距高点回撤/ATR% 超阈值；`data_quality` 异常/延迟。命中触发降级或否决。

### 9.2 信号合成
```
composite = w1*market + w2*sector_trend + w3*fund_flow + w4*etf_rs  （权重和=1）
risk_hit  = risk_filter(...)
if risk_hit 含 "否决条件"(大盘BEAR 且 数据缺失): 输出 暂不参与/禁止追高
elif risk_hit 含 "降级条件"(追高/高波动):        composite 下调一档
```

### 9.3 策略版本不可覆盖
- `strategy_hash = SHA256(json.dumps(rules+params, sort_keys=True, separators=(",",":"), ensure_ascii=False))`（规范化 JSON，避免字段顺序不同生成不同版本）。
- 版本号形如 `v1.0.0-<hash前6位>`；启动时计算 hash，若库中已存在同 hash 版本则复用，否则插入新版本。
- 旧版本**禁止 UPDATE**（唯一约束 + 写保护）；参数变更必然产生新版本，保证历史信号可复现。

### 9.4 公共建议档位（无持仓时，前端文字化展示）
| 档位 | 触发 |
|---|---|
| 暂不参与 | 数据不全/刚上市/频率限制，或风险否决 |
| 加入观察 | 60 ≤ composite < 75，关键规则部分命中 |
| 允许小仓位试错 | composite ≥ 75 且 risk 未命中否决/降级 |
| 机会增强 | composite ≥ 85 且 资金持续性/相对强弱双强 |
| 禁止追高 | risk_filter 命中追高 |
| 市场风险较高 | market_regime ∈ WEAK/BEAR 或高波动 |

### 9.5 持仓分析动作（提交持仓后由 `/api/portfolio/analyze` 返回）
| 动作 | 触发 |
|---|---|
| 继续持有 | 市场环境正常，ETF 相对强弱为正，未触发退出条件 |
| 降低仓位 | composite 下降或 risk_flag 命中降级但非否决 |
| 触发退出条件 | 跌破短期趋势线 / 相对强弱转负 / market_regime→BEAR |
| 等待重新确认 | 信号模糊或数据 STALE |

### 9.6 仓位表达（第一版前端文字为主）
- 内部仍保留 `suggested_position_range` 数值（如 [20,30]）。
- 前端**先以文字展示**：`不新增` / `轻仓试错` / `维持低仓位` / `逐步降低风险敞口`。
- 待策略充分回测后，再决定是否向用户暴露精确百分比。

每条返回均含 `action/reason/risk/suggested_position_*/invalidation_conditions/review_time`，全部由规则引擎确定性填充，模板只拼中文不改数值。

---

## 10. 分阶段开发计划（每阶段一个可独立测试/提交的模块）

| 阶段 | 模块 | 交付物 | 验证方式 |
|---|---|---|---|
| **P-1** ✅ | 基础行情接口验证 | 已完成：6/6 接口取到真实数据（sina/ths/tx），记录函数名/字段/空值/缺失，产出 `backend/scripts/p1_output/report.json` 与样例 | 实拉验证通过；详见 §3.1 |
| **P-1b** ✅ | 策略历史数据闭环验证 | 已完成：指数历史(tx) ✅；板块历史/资金流历史 em-only（生产验证）；历史涨跌家数靠每日累计。产出 `backend/scripts/p1b_output/report.json` | 实拉验证通过；详见 §3.1 |
| **P0** | 项目骨架 + 配置 | config/skeleton、`logging_conf`、pytest、两入口(main/worker) | `pytest` 空跑；读配置成功 |
| **P1** | SQLite 模型与索引 | **8 张核心表** ORM + 索引(§5.6) + `init_db.py` | 建表+索引成功；CRUD；含新增字段(metric_source/source_timestamp/mapping_version 等) |
| **P2** | 采集、切源一致性、数据质量 | `collector` + 多源降级一致 + `data_quality` + 每日 breadth 累计 | 盘中跑一次；切源重积累+降置信度；故障重试可见 |
| **P3** | 指标与策略 | indicator/sector/strategy(+versioning)/risk/opinion | 给定输入，信号字段完整、版本不可覆盖；资金持续性仅同源计算 |
| **P4** | FastAPI 查询接口 | `api`（**无鉴权层**）+ 轮询端点 | httpx/pytest 测端点 |
| **P5** | Vue 核心页面 | 5 个页面 + ECharts + 30s 轮询 | 本地 build + 浏览器手测 |
| **P6** | 按需持仓分析接口 | `portfolio/analyzer` + `/api/portfolio/analyze`（含校验） | 提交持仓即时返回，确认不落库 |
| **P7** | 日线回测 | `backtest_engine` + **新增 2 张回测表** + `/api/backtest/*` | 小样本回测，指标正确、无未来数据 |
| **P8** | 部署与备份 | nginx(Basic Auth)+etf-api(1 worker)/etf-worker + `db_backup` 脚本 | 服务器起服务，curl 通；验证 `.backup` |
| **P9** | （按需）用户系统 | 仅当确需保存个人持仓/历史建议时再做 | — |

**建议开发顺序（后端最小闭环优先）**：P0 骨架 → P1 数据库(含新字段/索引) → P2 真实采集入库 → 3 个基础查询 API → 前 3 个核心前端页面（总览/板块/ETF列表）。P0/P1 可现在开始，不必等 P-1b（已提前完成）；P-1b 须在 P3 前完成（已满足）。

**备份策略（P8 落实）**：
- 本地每日 `sqlite3 database.db ".backup" backup_YYYYMMDD.db`，压缩，保留 7 天。
- 每周上传一份至异地服务器或对象存储（防磁盘损坏）。
- 应用日志保留 14–30 天（logging 轮转）。
- 旧盘中快照（SNAPSHOT/BAR）保留 1–2 年后归档或清理。

**每阶段交付均包含**：文件路径、完整代码、设计说明、运行命令、测试方法、已知限制、下一步工作。

---

## 后续动作

设计已升级至 V5，固化了 P-1（基础行情）与 P-1b（策略历史数据闭环）两项验证结论，并补齐你提出的 8 项修改：多源切源一致性字段、source_timestamp 时间语义、ETF 映射版本化、strategy_hash 规范化、Nginx 端口隔离、回测异步化、核心索引、P-1b 历史闭环。

至此**数据源能否支持文档中每条策略规则已确认**：指数历史可达；板块历史/资金流历史依赖生产 em；历史涨跌家数靠每日累计且上线前不启用 breadth 相关规则。**设计可基本冻结。**

请 owner 最后确认两点即可开工：
1. 第 0 节假设（尤其 #11 环境、§9 权重初值、P-1b 闭环结论是否认可）。
2. 公共 6 档 + 持仓 4 档映射、以及 9.6 文字化仓位表达。

确认后从 **P0（项目骨架 + 配置 + 日志 + 两入口）** 开始，按"后端最小闭环优先"顺序推进。每模块附文件/代码/说明/运行/测试/限制/下一步。
