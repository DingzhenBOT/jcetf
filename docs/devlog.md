# 开发日志 / Dev Log

> 机制：每轮任务追加一条记录，保证跨轮连续性（已确认需求：每轮做开发文档记录）。
> 设计基线：`DESIGN.md`（V5，已冻结）。阶段计划见 DESIGN §10。
> 技能：本轮调用 `fullstack-dev` 落地后端骨架（集中配置/fail-fast、类型化错误、结构化 JSON 日志、健康检查、优雅关闭、显式 CORS、安全头）。

---

## 轮次 P0 — 项目骨架 + 配置 + 日志 + 两入口 ✅

**日期**：沙箱 2026-07-18｜**状态**：完成并通过测试（9 passed）+ 实跑冒烟（uvicorn / health / ready / worker 单实例）。

### 背景（承上）
- **P-1 ✅**：6/6 基础行情接口取到真实数据（sina/ths/tx），函数名/字段/空值/缺失已落 `backend/scripts/p1_output/report.json`。
- **P-1b ✅**：策略历史闭环验证，结论落 `backend/scripts/p1b_output/report.json`（指数历史 tx 可达；板块历史/资金流历史 em-only；涨跌家数靠每日累计）。
- **V5 冻结**：多源切源一致性字段、`source_timestamp`、ETF 映射版本化、`strategy_hash` 规范化、Nginx 端口隔离、回测异步化、核心索引、P-1b 历史闭环均已固化。

### 本轮交付清单（文件 → 职责）
| 文件 | 职责 | 关键设计 |
|---|---|---|
| `config/settings.yaml` | 集中配置（频率/阶段/阈值/白名单/数据源/路径/日志/调度/安全） | 可入库；相对路径以本文件目录为基准 |
| `config/.env.example` | 环境变量示例（仅示例，无真实密钥） | 由 systemd `EnvironmentFile` 或 `source` 注入，不自动加载 |
| `backend/app/config.py` | 配置加载 + fail-fast 校验 + 路径解析 + 单例 | YAML 主 + 白名单 env 覆盖；非法组合启动即失败；`get_settings()` 单例 |
| `backend/app/errors.py` | 类型化错误层级 | `AppError` + `NotFoundError`/`ValidationError`/`ConflictError`/`DataSourceError`/`UnavailableError`/`ConfigError` |
| `backend/app/logging_conf.py` | 结构化 JSON 日志 + 轮转 + request_id | JSON/可读双格式；`TimedRotatingFileHandler` 按日保留 14 天；`contextvars` 贯穿 |
| `backend/app/main.py` | FastAPI 入口（etf-api，1 worker，**无鉴权层**） | `/health` `/ready`、全局异常处理器、CORS 白名单、安全头、request_id 中间件、lifespan 优雅关闭 |
| `backend/app/worker.py` | APScheduler 入口（etf-worker，单实例） | `BlockingScheduler`(Asia/Shanghai)；`fcntl` 单实例锁；SIGTERM/SIGINT 优雅关闭；P0 仅占位 `health_heartbeat` |
| `backend/app/__init__.py` `backend/tests/__init__.py` | 包初始化 | — |
| `backend/requirements.txt` | 依赖固定（按沙箱已验证版本） | fastapi/uvicorn/sqlalchemy/apscheduler/pydantic/pyyaml/pandas/akshare/pytest |
| `backend/pytest.ini` | pytest 配置 | `pythonpath=.` 使 `app` 可导入 |
| `backend/tests/conftest.py` `test_config.py` `test_health.py` | 测试骨架 | 配置加载/失败、env 覆盖、prod 守卫；/health、/ready、安全头、request_id |

### 对齐 DESIGN / 与原设计的取舍
- **遵循 V5「FastAPI 无鉴权层」**：本进程不实现 auth 中间件；鉴权全在 Nginx（Basic Auth + HTTPS）。`fullstack-dev` 清单里的 auth 项在此**主动省略并注明理由**。
- **安全头兜底**：DESIGN 说 Nginx 统一设；P0 仍在 API 内加一层 `X-Content-Type-Options/X-Frame-Options/Referrer-Policy/CSP`，双保险，可在 `security.enable_headers=false` 关闭。
- **CORS 显式白名单**：dev 含 `:5173`（vite）；prod 由 Nginx 同源托管，可留空。**绝不 `*`**。
- **优雅关闭**：FastAPI 用 `lifespan`；worker 用信号处理器 + `scheduler.shutdown(wait=False)`。
- **fail-fast 落地点**：① 配置文件缺失/非法 YAML/类型错误 → `ConfigError`；② prod 绑定非回环地址 → 失败；③ prod 用 mock 数据源 → 失败；④ 非法 env 覆盖值 → 失败。

### 验证方式（本轮已跑通）
```bash
cd /workspace/backend
python3.11 -m pytest                 # 9 passed
python3.11 -m uvicorn app.main:app --host 127.0.0.1 --port 8011   # 另开：curl /health /ready
python3.11 -m app.worker             # 再起一个应 exit 1（单实例锁生效）
```
- `/health` → 200，`status=ok`，带 `x-request-id` 与安全头。
- `/ready` → 200，`checks.config=ok`、`checks.data_dir_writable=ok`。
- worker 第二实例退出码 1，第一实例收到 SIGTERM 优雅停止。

### 已知限制（P0）
1. **无数据库**：`/ready` 尚未 ping DB；P1 接入 SQLite 后补 `db_ping` 检查。
2. **无业务路由**：`/api/market/*` 等 P4 挂载；P0 仅系统端点。
3. **worker 任务为占位**：`health_heartbeat` 每 5 分钟；采集/评估/回测/备份任务 P2+ 挂载。
4. **env 覆盖为白名单**：仅 12 个键可覆盖；`.env` 不自动加载（按 DESIGN 走 systemd `EnvironmentFile`）。
5. **mock 数据源开关已预留**（`data_source.mode=mock`），但 Mock 适配器 P2 才实现；当前设 mock 不影响 P0（无采集）。
6. **日志会写盘**：`data/logs/app.log`，测试运行也会产生（可接受，保留 14 天后轮转清理）。

### 下一步：P1（SQLite 模型与索引）
- 建 8 张核心表 ORM（`db/base.py` + `db/models/*`）+ §5.6 索引 + `db/session.py`（WAL + 单写者）。
- 新字段就位：`source_timestamp` / `metric_source` / `metric_definition_version` / `source_switched` / `etf_mapping.mapping_version` 等。
- `scripts/init_db.py`：建表 + 索引 + 首次注入 `strategy_version`。
- 把 `/ready` 接到真实 DB ping；新增 1–2 个最小查询端点（P4 前先打通「写→读」闭环）。

### 跨轮提示（给下一轮的自己）
- 配置改动优先改 `config/settings.yaml`；敏感/环境项加 env 白名单（改 `config.py` 的 `_ENV_OVERRIDES`）。
- 新增模块请保持「Controller 不含业务 / Service 不 import HTTP / 业务不直接 import AkShare」。
- 所有可预期错误抛 `AppError` 子类，禁止裸 `Exception`。
- 时间统一 UTC 存；`trading_date` 按北京时间判定；前端再转。

---

## 轮次 P0b — 日志/数据持久化（防撑爆）+ GitHub 工作流

**触发**：用户确认 4 核 4GB / 60GB 磁盘下，需要「定期清理 + 关键数据保留」机制；并希望项目上 GitHub、关键测试节点暂停以便自测。

### 持久化机制（已落地，可独立测试）
| 关注点 | 机制 | 默认保留 | 备注 |
|---|---|---|---|
| 应用日志 | `TimedRotatingFileHandler` 按日轮转 + `cleanup_old_logs` 兜底 | 14 天 | 兜底防进程长期关闭后旧文件堆积 |
| 盘中快照 | `prune_market_quotes` 删 `data_kind='SNAPSHOT'` | 90 天 | 最占空间，热窗口短 |
| 日线 BAR | 同上删 `data_kind='BAR'` | 730 天 | 约 2 年 |
| 信号/意见 | （P1 建表后补，列名定后加） | 730 天 | 当前仅 market_quote 参与清理 |
| 库备份 | `scripts/db_backup.py`：`sqlite3.backup()`+gzip，本地保留 | 7 天 | 异地周备 P8；用 `.backup` 非 `cp`（DESIGN §0） |
| 空间回收 | 清理后 `VACUUM`（autocommit 下） | — | `vacuum_after_prune` 开关 |
| 磁盘守卫 | `health_heartbeat` 每 5 分钟查使用率 | 阈值 85% | **只告警不自动删业务数据** |
| 总开关 | `housekeeping.disabled` | false | 紧急可一键关清理 |

**交付文件**
- `config/settings.yaml`：新增 `housekeeping` 段（各保留天数 / 备份保留 / 磁盘阈值 / 开关）。
- `backend/app/config.py`：新增 `HousekeepingConfig` 并入 `Settings`。
- `backend/app/retention.py`：`prune_market_quotes`（表存在才动，P1 前 no-op）、`vacuum`、`run_retention`、`cleanup_old_logs`、`check_disk_usage`。
- `backend/scripts/db_backup.py`：`run_backup()`（CLI + import）、本地保留、远程占位 hook。
- `backend/app/worker.py`：新增 `db_backup`(02:00) / `log_cleanup`(02:05) / `data_retention`(02:10) 三个 cron 任务 + `run_job` 包装；`health_heartbeat` 增加磁盘检查。
- `backend/tests/test_housekeeping.py`：6 个用例（磁盘检查 / 缺库 no-op / 缺表 no-op / 日志清理 / 备份 gzip / 备份保留）。

**验证**：`pytest` 15 passed；`python3.11 -m scripts.db_backup`（db 不存在 skip；造库后生成有效 gzip）；worker 启动注册任务并优雅停止。

### 关键约束（给 P1 的自己）
> `prune` 用 SQLite `datetime('now','-N day')` 比较 `timestamp`，**要求 P1 的 timestamp 列存「naive UTC ISO」**（如 `2026-07-18 13:00:00`），不可带 `+00:00`/`Z`，否则 datetime() 解析失败。

### GitHub 工作流（已确认采用）
- 沙箱 `gh` **未登录**，无法直接 push；本轮回先本地 `git init` + 提交基线。
- **里程碑暂停**：每个阶段（P0/P1/…）完成后提交并暂停，等你自测，确认后继续。
- **已上 GitHub**：仓库 `https://github.com/DingzhenBOT/jcetf.git`，分支 `main`，基线 commit `1e1b55e`（42 文件）已 push。token 经临时 URL 传入并立即擦除，`.git/config` 无残留。后续里程碑复用该 PAT 或新建短期 token 即可。

---

## 轮次 P1 — SQLite 8 张核心表 + 索引 + init_db ✅

**交付文件**
| 文件 | 职责 |
|---|---|
| `backend/app/db/base.py` | `Base` + `utcnow()`（naive UTC，满足 prune 比较约束） |
| `backend/app/db/session.py` | `make_engine`(WAL+busy_timeout)、`session_scope`、`ping_db`、`init_db`(建表+幂等注入 strategy_version) |
| `backend/app/db/models/{market,mapping,signal_opinion,system}.py` | 8 张核心表 |
| `backend/app/db/models/__init__.py` `db/__init__.py` | 模型注册入口 |
| `backend/app/strategy_versioning.py` | `compute_strategy_hash`(SHA256 规范化) / `build_version_string` / `current_strategy_version` |
| `backend/scripts/init_db.py` | CLI 建库（幂等，可重复跑） |
| `backend/app/retention.py` | 复用 `make_engine`（去重，去掉本地副本） |
| `backend/app/main.py` | `/ready` 接入 `ping_db` |
| `backend/tests/test_db.py` | 7 用例（建表/唯一约束/索引/版本幂等/prune/hash/ping） |

**Schema 要点（对齐 DESIGN §5）**
- 时间列统一 **naive UTC**（关键：prune 用 `datetime('now','-N day')` 比较，tz-aware 会破坏排序）。
- `market_quote` 具名唯一索引 `uq_market_quote` = `data_source+symbol_type+symbol+data_kind+timeframe+timestamp` → 幂等写入（采集重试不重复）。
- §5.6 四个核心索引全部建成：`idx_quote_symbol_time` / `idx_quote_trade_type` / `idx_signal_etf_time` / `idx_task_name_time`（外加各表辅助索引）。
- `strategy_version` 不可覆盖：唯一约束 + 写保护；P1 用当前 params 注入基线 **`v1.0.0-eb76a0`**，`rules_json={}`；P3 填实际规则后 hash 变化 → 自动新版本。
- **坑**：SQLite 下 `UniqueConstraint` 会变内联自动索引（无名），故改用 `Index(..., unique=True)` 才得到具名唯一索引（与 DESIGN「唯一键」一致、可稳定引用）。

**验证**：`pytest` **22 passed**；`python3.11 -m scripts.init_db` 实跑建出 `data/etf_monitor.db`（8 表 + 1 行 strategy_version）。`/ready` 现含 `db: ok`。

**已知限制（P1）**
1. `etf_mapping` 尚未 seed（手动映射，P2 `seed_mapping` 落地）。
2. `strategy_version.rules_json` 暂空（P3 填充真正规则）。
3. 回测两张表（`backtest_run`/`backtest_trade`）留到 P7。
4. 真实采集未开始，`data_source_status`/`market_quote` 为空，待 P2。

**下一步：P2（采集 / 切源一致性 / 数据质量）**
- `collector` + 多源降级（em→sina/ths/tx）+ `normalize` 统一模型。
- `data_quality` 标记 OK/STALE/MISSING/DELAY/ANOMALY。
- 每日 `market_breadth` 累计（无历史 API，见 §3.1）。
- `post_collection_evaluate` 占位（P3 填规则）。

### 跨轮提示（给下一轮的自己）
- 写时间列一律用 `db.base.utcnow()` 或 naive UTC；**禁止 tz-aware datetime 入库**。
- 新增唯一约束请用 `Index(..., unique=True)`，别用 `UniqueConstraint`（SQLite 无名）。
- 引擎/会话只走 `app.db.session`，不要在模块里自建 engine。

---

## 轮次 P2 — 采集 / 多源降级一致性 / 数据质量 / 每日 breadth 累计 ✅

**交付文件**
| 文件 | 职责 | 关键设计 |
|---|---|---|
| `backend/app/data_provider/__init__.py` | `build_provider` 工厂 | `real`→`AkShareAdapter`；`mock` 暂未实现（DESIGN §0 禁止无来源降级 Mock） |
| `backend/app/data_provider/akshare_adapter.py` | 多源可插拔 + 自动降级 | preferred→fallback 顺序；首个成功即返回并记 `df.attrs['__source']`（P1b/P2 起已写） |
| `backend/app/collector/normalize.py` | 中文列 → `market_quote`/`market_breadth` 字典 | 指数/ETF/板块异构列统一映射；缺失写 `None`；`source_timestamp` 北京→UTC |
| `backend/app/collector/collector.py` | 采集编排 | provider→normalize→质量→切源标记→幂等入库→数据源状态；单能力失败不连坐 |
| `backend/app/data_quality/checker.py` | 逐条质量评估 | OK/STALE/MISSING/DELAY/ANOMALY；仅交易时段严格校验时间新鲜度 |
| `backend/app/market_calendar/__init__.py` | 交易日历单点判断 | 北京=UTC+8；日历优先数据源加载，失败回退「周一~周五」启发式；交易时段 09:30-11:30/13:00-15:00 |
| `backend/app/repository/quote_repo.py` | 写入层 | `market_quote` 走 `ON CONFLICT DO UPDATE` 幂等；`breadth` 按 `data_source+trading_date` 每日一条；`data_source_status` upsert |
| `backend/app/config.py` | 新增 `DataQualityConfig` | delay/stale 阈值、涨跌幅护栏、最小价 |
| `config/settings.yaml` | 新增 `data_quality` 段 | 与生产默认值一致 |
| `backend/app/worker.py` | 挂载 P2 采集任务 | `pre_market_prepare`(08:50) / `intraday_collect`(每 interval，内部 is_trading_now 守卫) / `midday_breadth`(11:35) / `post_close_review`(15:10)；启动期加载日历 |
| `backend/tests/test_{normalize,data_quality,market_calendar,collector}.py` | 22 新增用例 | 列映射 / 质量判定 / 日历 / 编排+幂等+切源+失败路径 |

**验证（本轮已跑通）**
```bash
cd /workspace/backend
python3.11 -m pytest            # 44 passed（P0/P1 22 + P2 22）
# 真实端到端采集（沙箱可达 sina/ths，强制 preferred=sina）：
python3.11 -c "..."             # collect_market: index 562(sina) / etf 1602(sina) / industry 90(ths) / concept 386(ths)
                               # breadth: rise=482 fall=5000 limit_up=44；market_quote 2640 行；data_source_status 全 OK
```
- 单元测试覆盖：列映射（em 板块代码 vs ths 行业名称）、缺失列→`None`、breadth 计数+时间戳解析；质量 OK/MISSING/ANOMALY/STALE/DELAY 且收盘后不惩罚陈旧；日历北京偏移/交易时段/周末跳过；采集入库行数、同时间戳幂等、切源 `source_switched=1`、单能力失败记 FAILED 且其他正常、breadth 每日幂等。
- **真实数据闭环验证**：用 `AkShareAdapter` 对 sina/ths 实拉，4 类快照全部成功入库，breadth 真实累计；证明适配器函数名与生产降级路径在沙箱可用（生产服务器优先 em，路径一致）。

**对齐 DESIGN / 取舍**
- **多源一致性（R7/§3.1）**：`metric_source=source`，资金持续性仅同源计算；切源时本批次 `source_switched=1`（对比该 `symbol_type` 上一条数据源），策略引擎据此降置信/重积累窗口。
- **时间语义**：快照源无时间戳列 → `source_timestamp=None, timestamp=collected_at`；breadth 的 `时间戳` 解析为北京时间再转 UTC。质量新鲜度仅交易时段对带 `source_timestamp` 的行生效，避免收盘后误标 STALE。
- **幂等写入**：`market_quote` 靠具名唯一索引 `uq_market_quote` + `ON CONFLICT DO UPDATE`；采集重试/重跑不重复插。注：每 3 分钟快照天然产生新 `timestamp` 行（保留时间序列），幂等性保护的是「同调度重复触发」而非「重采集合并」。
- **缺失字段不臆造**：em 专属的 `large_order_inflow`、板块涨跌家数/涨跌停数在沙箱（sina/ths）为空，normalize 写 `None`，质量/策略层降级而非报错（DESIGN §3.1 注）。
- **板块来源异构**：em 用「板块代码」，ths 用「行业/概念 名称」作为 `symbol`；生产（em）与沙箱（ths）板块标识空间不同 —— 这是已知跨源身份差异，**`etf_mapping` 的 sector 关联必须 P3 统一身份后再 seed**（见限制）。

**已知限制（P2）**
1. **`etf_mapping` 暂不 seed**：沙箱 sector 以 ths 名称入库，生产以 em 代码入库，身份未对齐；P3 策略引擎做 ETF→板块关联前需先统一 sector 身份，故 mapping 留 P3。
2. **breadth 涨停/跌停阈值 9.5%**：近似覆盖主板 ±10% 与 ST ±5% 不分别处理；历史涨跌家数无 API，上线前 breadth 相关规则不启用（DESIGN §3.1 警告）。
3. **`post_collection_evaluate` 未实现**：P2 仅把数据落库并打质量标；指标/信号在 P3。
4. **盘中快照频率**：`intraday_collect` 按 `intraday_interval_seconds`（默认 180s）全天跑，由 `is_trading_now` 守卫；非交易时段空转跳过（轻量）。`pre_close` 提频未单独建任务，复用同 interval（如需更密可后续加）。
5. **mock 数据源未实现**：`data_source.mode=mock` 会抛 `NotImplementedError`；dev/test 用 `FakeProvider` 注入（见 `test_collector.py`），不接全局 Mock 适配器。

**下一步：P3（指标与策略）**
- `indicator_engine`（MA/动量/RSI/MACD/量能/波动率）仅吃 BAR（不吃 SNAPSHOT，R14）。
- `sector_engine` 板块强弱 + 资金持续性（仅同源 `metric_source`）。
- `strategy_engine` + `risk_engine`：5 类评分→公共 6 档信号，`strategy_hash` 不可覆盖；`etf_mapping` 在此 seed（先统一 sector 身份）。
- `opinion_engine`：模板化盘中/收盘意见（LLM 仅润色不判断）。
- 接通 `post_collection_evaluate`（采集后算指标→查阈值→出信号）。

### 已知限制（P0b）
1. 清理目前只覆盖 `market_quote` 的 SNAPSHOT/BAR；opinion/signal 表清理待 P1 定列后补。
2. 异地周备未实现（P8）；`backup_remote_enabled=true` 时仅告警不静默失败。
3. 磁盘守卫只告警不处置（避免误删业务数据），处置靠保留策略 + 手动。

### 下一步
- **P1**：8 张核心表 ORM + 索引 + `init_db.py`，并落实 `timestamp` naive UTC 约束；`/ready` 接 DB ping；把 opinion/signal 清理接入 `run_retention`。
- GitHub：等你给仓库信息后 push 基线。

---

## P3 — 指标与策略引擎（已交付，待真机自测）

> 状态：代码已写完、单测全绿（94 passed）、离线端到端冒烟通过（seed 16 映射 → 16 信号/16 意见，幂等重跑稳定，strategy_version 不可覆盖两行）。
> 计划文件：`/root/.codebuddy/plans/stellar-beacon-newton.md`

### 交付文件（相对 `/workspace/backend`）
| 文件 | 变更 |
|---|---|
| `app/config.py` | 新增 `BackfillConfig`（lookback_days/broad_index_codes/major_sector_codes）；`StrategyConfig.broad_index_codes`（D5，加法不改既有 YAML） |
| `app/strategy_versioning.py` | 新增 `mint_strategy_version(session, settings, rules)`：hash 不同→插入新不可覆盖行；已存在则复用，绝不 UPDATE |
| `app/repository/quote_repo.py` | 新增读函数：`get_latest_quote` / `get_bar_history` / `get_max_bar_timestamp` / `get_breadth_on_date` / `get_sector_quotes`（复用既有索引） |
| `app/repository/mapping_repo.py` | 新增：`get_active_mappings`（按 as_of 生效窗）/ `upsert_mapping`（etf_code+mapping_version 幂等）/ `get_mappings_for_backfill` |
| `app/repository/__init__.py` | 导出上述函数 |
| `app/collector/normalize.py` | 新增 `normalize_etf_bar` / `normalize_index_bar` / `normalize_sector_bar` / `normalize_sector_fund_flow_bar`（data_kind=BAR, timeframe=1d，timestamp=交易日 UTC 午夜，metric_source=source） |
| `app/collector/collector.py` | 新增 `_collect_bar` + 四类 `collect_*_history` + `backfill_history`（增量按 max(timestamp)+1；em-only 板块历史失败非致命，D4） |
| `app/indicator_engine/{__init__,indicators,engine}.py` | 纯 pandas 指标（SMA/斜率/RSI(Wilder)/MACD/动量/动量分位/量比/ATR/ATR%/滚动RS）+ `IndicatorEngine.compute(bar_df, benchmark_close)`；只吃 BAR |
| `app/sector_engine/{__init__,engine}.py` | 板块趋势评分 + 资金持续性（**仅同 metric_source**） |
| `app/strategy_engine/{__init__,rules,engine}.py` | `RULES_V1` 冻结规则字典（DESIGN §9 转录）；`StrategyEngine.evaluate_etf`；纯函数 `compute_composite`/`decide_tier`（缺失重归一化+降置信，D4） |
| `app/risk_engine/{__init__,engine}.py` | veto / downgrade / high_vol / chase_high，受 `settings.strategy.risk_filter` 开关约束 |
| `app/opinion_engine/{__init__,phrase,templates,engine}.py` | `template-v1` 确定性生成（D1：默认 `TemplatePhraseClient` 无 LLM；`LLMPhraseClient` 桩禁用） |
| `app/evaluation/{__init__,pipeline}.py` | `post_collection_evaluate(session, settings, *, phase, as_of)`：mint 版本→逐映射评估→**幂等 upsert** Signal/Opinion |
| `app/worker.py` | 新增 `job_backfill_history`(16:30) / `job_pre_close_evaluate`(14:59) / `job_post_close_evaluate`(15:10)，均交易日历守卫 |
| `scripts/seed_mapping.py` | 16 支 ETF→`etf_mapping`（valid_from=2000-01-01 对任何 as_of 生效；幂等） |
| `scripts/run_evaluate.py` | 一次性 `post_collection_evaluate`（--phase / --backfill） |
| `scripts/collect_once.py` | 增加 `--backfill`（仅回填历史 BAR） |
| `tests/test_*.py`（7 个） | indicator/strategy/risk/opinion/pipeline_idempotency/repository_read/collector_history，共 50 例 |

**架构约束已遵守**：引擎层不开 HTTP、不碰 `fastapi`/Request；所有 Session 写操作集中在 `evaluation/pipeline.py` 与 `worker.py`；引擎为纯函数返回 dict。

### 默认决策（计划 D1-D5，已落地）
- **D1**：意见仅模板生成，`LLMPhraseClient` 为禁用桩（DESIGN §0：LLM 只润色不判断）。
- **D2**：`Signal.signal_type` 存英文档位码（`NO_PARTICIPATE`/`OBSERVE`/`SMALL_POSITION`/`OPPORTUNITY_ENHANCE`/`NO_CHASE_HIGH`/`MARKET_RISK_HIGH`），中文在 `suggested_action` + opinion。
- **D3**：`post_collection_evaluate` 每 (trading_date,target_etf,version) 写一条 Signal（原地更新幂等）；每 (trading_date,signal_id,phase) 写一条 Opinion。`pre_close`+`post_close` 两档评估。
- **D4**：缺失 sector/fund_flow/etf_rs 数据→**不自动否决**，综合分对可用项重归一化、降置信（每缺一项 -15）。唯一硬否决 = 大盘 BEAR **且** 宽基/宽度数据缺失。
- **D5**：加法配置 `BackfillConfig` + `strategy.broad_index_codes`，既有 YAML 仍可直接加载。

### 已知限制（P3，重要 → 真机表现）
1. **板块历史/资金流历史在用户服务器也取不到**（与沙箱一致：P2 实测返回 sina/ths 而非 em）。因此 `stock_board_industry_hist_em` / `stock_sector_fund_flow_hist` 失败 → `sector_trend_score` 与 `fund_flow_score` 在**沙箱与用户服务器均为 None**。`composite` 仅由 `market_score`（宽基指数 BAR + 宽度）+ `etf_rs`（ETF vs 宽基指数）构成；板块评分只在 em 可达时激活。
2. **板块身份差异**：seed 用 em 板块代码（BKxxxx），而沙箱 ths 回落返回板块名称；即使板块历史可达，`related_sector_codes` 也可能 join 不到任何 BAR → 引擎降级（D4），不崩溃。
3. **首跑回填联网重**：~16 ETF + 3 宽基 + ~10 板块 ×250d；按 max(timestamp) 增量续拉。
4. **`market_regime` 依赖 breadth**：breadth 仅交易时段累计（P2）。盘前评估可能缺同日 breadth→`advance_ratio` 缺失→`market_score` 部分降级（非否决，除非 BEAR+缺失）。
5. **RS 同业排名**：缺 peer 集时回退宽基指数作基准（已实现）；纯 ETF 间排名待 P7 回测数据。
6. **LLM 润色未接**（D1）：`content` 为模板文案，P3 不含自然语言润色。
7. **无 schema 变更**：P3 复用 P1/P2 全部表，用户既有 P2 库无需迁移即可跑（Opinion 未加 target_etf 列，意见以 `signal_id+phase` 唯一键幂等，避免 ALTER TABLE 破坏既有库）。

### 真机自测步骤（用户服务器）
```bash
cd /workspace/backend
python3.11 -m pip install -r backend/requirements.txt   # 沙箱已满足，服务器按需
python3.11 -m scripts.init_db                           # 建表 + 注入 baseline strategy_version
python3.11 -m scripts.seed_mapping                      # 16 ETF 映射（幂等）
python3.11 -m scripts.run_evaluate --phase post_close   # 离线评估（无 BAR 时全 NO_PARTICIPATE，验证链路）
python3.11 -m scripts.collect_once --backfill           # 回填历史 BAR（联网；板块历史会 FAILED，属预期）
python3.11 -m scripts.run_evaluate --phase post_close   # 有 BAR 后重评，信号应开始分化
# sqlite 校验
sqlite3 ../data/etf_monitor.db "SELECT target_etf,signal_type,score,confidence,market_regime,strategy_version FROM signal ORDER BY target_etf;"
sqlite3 ../data/etf_monitor.db "SELECT count(*) FROM opinion; SELECT version,strategy_hash FROM strategy_version;"  # 应为 2 行
# 幂等：重跑 run_evaluate，signal/opinion 行数不变
python3.11 -m pytest -q                                  # 94 passed
```
> 注：计划 §9 写的 `cd /workspace` 是笔误，脚本实际在 `backend/scripts`，需从 `/workspace/backend` 运行。

### 下一步：P4（FastAPI 查询接口）
- `GET /api/signals/latest`（前端 30s 轮询）、`GET /api/etfs`、`GET /api/signals/history`、`GET /api/opinions/{etf}`。
- 无鉴权层（DESIGN §0）；只读 SQLite，复用 `repository` 读函数。
- 前端轮询见于 P5。

---

## P4 — FastAPI 查询接口（已完成，2026-07-19）

> 用户确认 P4 范围 = devlog 4 端点 **+** `/api/market/breadth/latest` + `/api/market/overview`（让 P5 总览页 30s 轮询开箱即用）。
> 铁律保持：LLM 只润色不判断；DESIGN §9 冻结；strategy_hash 不可覆盖；API 无鉴权层。

### 交付文件
| 文件 | 作用 |
|---|---|
| `app/api/__init__.py` | API 包标识 |
| `app/api/deps.py` | `get_db` 依赖 + `build_read_engine`（只读引擎，`PRAGMA query_only=ON` 兜底防误写） |
| `app/api/schemas.py` | Pydantic 响应模型（SignalOut/OpinionOut/EtfListItem/SignalHistoryPage/OpinionsForEtf/BreadthOut/IndexSnapshotOut/MarketOverviewOut） |
| `app/api/serializers.py` | ORM→dict：档位中文映射（TIER_TEXT / position_text_of）、时间 ISO 化（naive UTC） |
| `app/api/routers/{signals,etfs,opinions,market}.py` | 6 个端点 |
| `app/api/routers/__init__.py` | 聚合 router |
| `app/repository/signal_repo.py` | **只读** 信号/意见查询（get_latest_signals / get_latest_signal_for_etf / get_signal_history / get_opinions_for_etf） |
| `app/repository/quote_repo.py` | 新增 `get_latest_breadth` |
| `app/repository/__init__.py` | 导出上述读函数 |
| `app/main.py` | lifespan 创建只读引擎存入 `app.state.db_factory` 并 shutdown dispose；`create_app` 挂载 4 个 router（tags 分组） |
| `tests/conftest.py` | 新增 `api_client` / `api_client_no_breadth` fixtures（临时 SQLite 播种映射/信号/意见/宽度/指数 BAR + 依赖覆盖） |
| `tests/test_api_signals.py` / `test_api_etfs.py` / `test_api_opinions.py` / `test_api_market.py` | 端点测试（17 例） |

### 端点契约
- `GET /api/signals/latest`：每支生效 ETF 最新一条（`MAX(generated_at)`）；空库返回 `[]`。
- `GET /api/signals/history`：`?etf_code=&trading_date=YYYY-MM-DD&limit=1..200&offset>=0`；非法日期/越界 → 422；返回 `{items,total,limit,offset}`。
- `GET /api/etfs`：ETF 列表含 `latest_signal`（无信号则 `null`）。
- `GET /api/opinions/{etf}`：未知 ETF → 404；`?phase=` 非法 → 422；按 `generated_at desc`；可空列表。
- `GET /api/market/breadth/latest`：最新宽度（含 `advance_ratio`）；无数据返回字段全 `null`（不 404）。
- `GET /api/market/overview`：宽基指数最新 BAR + 宽度 + `signal_risk` 汇总（只读统计，非规则重算）；`as_of` 取最大交易日。

### 关键约定
- **只读**：API 进程独立只读引擎（`query_only=ON` 已验证拦截写 → OperationalError）；与 worker 共享 SQLite（WAL 并发读）。
- **档位中文**：响应同时给 `signal_type`（英文码）+ `signal_type_text`（中文）+ `position_text`（文字仓位），前端无需重实现映射。
- **降级兼容**：em 不可达导致 `failed_rules` 含 `broad_index_missing`/`breadth_missing` 等时原样返回，前端据以标「观察期数据不足」。
- **未破坏冻结契约**：仅新增读函数与路由，未动 `strategy_engine`/`opinion_engine`/DESIGN §9；`strategy_version` 仍不可覆盖。

### 验证
- `python3.11 -m pytest -q` → **111 passed**（P3 94 + P4 17）。
- 真实库（`data/etf_monitor.db` 已有 16 信号）冒烟：6 端点均 200/404 符合预期（沙箱库无 mapping/signal 故部分返回空，逻辑由测试覆盖）。
- 只读引擎写拦截验证通过。

### 下一步：P5（Vue 核心页面）
基于 P4 已就绪的 `signals/latest` + `market/overview` + `market/breadth/latest`（30s 轮询双端齐备）+ `etfs` + `opinions/{etf}` 构建 5 页 + ECharts；可顺带补 `/api/opinions/current|history`。P6 持仓分析、P7 回测、P8 nginx 部署（Basic Auth + HTTPS + 反代 `/api/*`）。

---

## P5 — Vue 核心前端页面（已完成，2026-07-19）

> 状态：脚手架 + 6 端点类型化 API 层 + 30s 轮询 store + UI/图表组件 + 5 页面全部落地；`npm run build`（vue-tsc 类型检查 + vite 打包）通过（620 模块）；运行冒烟 5 路由 Playwright 截图 `PAGE_ERRORS: none`，无运行时 JS 错误。
> 铁律保持：前端只展示不判断（LLM 只润色不判断 / DESIGN §9 冻结 / strategy_hash 不可覆盖 全部在后端）；API 无鉴权层（DESIGN §0）。

### 技术栈
- Vue 3.4 + Vite 5.4 + TypeScript 5.5（strict）+ Tailwind 3.4 + ECharts 5.5 + vue-router 4（**hash 模式**，免 Nginx 额外 rewrite）。
- Node v22.13.1 / npm 11.9.0。
- A股惯例 **红涨绿跌**：Tailwind 语义色 `up=#dc2626` / `down=#16a34a` / `flat=#64748b`（禁纯黑、禁 Inter、禁 emoji，遵循 frontend-dev 硬规则；与 DESIGN 冲突以 DESIGN 为准）。
- 时间：后端 naive UTC ISO → 前端统一按北京时间（UTC+8）展示。
- 档位：严格复用后端 `TIER_TEXT` 中文映射（前后端同一份语义，不另造）。

### 交付文件（相对 `/workspace/frontend`）
| 文件 | 职责 |
|---|---|
| `package.json` / `vite.config.ts` / `tsconfig*.json` / `tailwind.config.js` / `postcss.config.js` / `index.html` | 脚手架：scripts（dev/build/type-check/preview）、`@`→`src`、`server.proxy['/api']→:8000`、`manualChunks` 拆 echarts/vue、`strict` + `@/*` 路径、system-ui 字体、up/down/flat 语义色、zh-CN |
| `src/vite-env.d.ts` | vite client + `*.vue` 模块声明（冗余根 `env.d.ts` 已删除，避免不被 tsconfig include 的游离文件） |
| `src/main.ts` / `src/App.vue` | 入口 + 根布局（`min-h-[100dvh]` flex 列；`onMounted` 启动 30s 轮询、`onUnmounted` 停止；footer 免责声明） |
| `src/styles/main.css` | tailwind 指令 + `tnum` 等宽数字 + 滚动条 + `prefers-reduced-motion` |
| `src/api/types.ts` | 接口严格镜像 P4 Pydantic schema（IndexSnapshot/Breadth/SignalRisk/MarketOverview/Signal/EtfListItem/SignalHistoryPage/Opinion/OpinionsForEtf） |
| `src/api/client.ts` | `ApiError` + `apiGet<T>`（fetch 封装、统一错误）；`API_BASE = import.meta.env.VITE_API_BASE ?? '/api'` |
| `src/api/endpoints.ts` | `getOverview/getBreadthLatest/getSignalsLatest/getSignalsHistory/getEtfs/getOpinions` |
| `src/lib/tier.ts` | `TIER_TEXT`/`TIER_ORDER`/`TIER_BADGE`/`TIER_COLOR`/`REGIME_TEXT`/`PHASE_TEXT` + 徽标完整类名（避免 Tailwind 动态拼接 JIT 失效） |
| `src/lib/format.ts` | `fmtPct/fmtNum/fmtInt/fmtAmountYi/fmtScore/fmtConfidence`（**confidence 后端为 0–100 整数百分比，直接 `Math.round(v)%` 展示**，修复了 ×100 导致 5500% 的 bug）/ `changeColor`（红涨绿跌） |
| `src/lib/time.ts` | `asUtc`（naive UTC 补 Z）/`toBeijing`/`toBeijingDate`/`toRelative`/`daysSinceBeijingDate` |
| `src/stores/market.ts` | 全局 30s 轮询 store：`tick()` 并行 `getOverview`+`getSignalsLatest`；`startPolling(30000)`/`stopPolling`/`refreshNow`；只轮询 DESIGN 指定的 overview+signals/latest |
| `src/components/ui/{Card,Badge,StatePanel,AppNav}.vue` | Card（title/subtitle+actions slot）、Badge（仅形状，text+class 由父传）、StatePanel（Loading 骨架/Error+重试/Empty/正常 四态，`role=alert`/`aria-live`）、AppNav（sticky 导航 + 风险徽标 + 连接状态点 + 相对更新时间，移动端响应式） |
| `src/components/charts/{BaseChart,BreadthChart,IndexBars,SignalRiskChart}.vue` | BaseChart（`echarts.init`+`setOption(opt,true)`+resize+dispose）；BreadthChart（pie 红涨绿跌）；IndexBars（横向 bar 涨红跌绿）；SignalRiskChart（pie TIER_COLOR） |
| `src/components/sections/{SignalTable,EtfTable,OpinionList}.vue` | SignalTable（`showEtf` 列）、EtfTable（行点击→详情）、OpinionList |
| `src/views/{MarketOverview,SectorView,EtfList,EtfDetail,SystemStatus}.vue` | 5 个页面（详见下） |
| `src/router/index.ts` | `createWebHashHistory()`；`/` `/sectors` `/etfs` `/etfs/:code` `/system` + 兜底 redirect |

### 页面与端点对接
- **市场总览 `/`**：标题 + 手动刷新；三栏（IndexBars/BreadthChart/SignalRiskChart）+ 最新信号表；由 30s 轮询 store 驱动（`overview` + `signals/latest`）。风险徽标 `风险 ${market_risk_level}`。
- **板块 SectorView `/sectors`**：按 `etf.category` 分组 + `related_sector_codes` 聚合展示；**诚实标注**「实时板块排行接口后续接入」（P4 未实现 `/api/sectors/*`，当前为派生视图）。
- **ETF 列表 `/etfs`**：搜索 + 分类筛选，按 `latest_signal.score` 排序。
- **ETF 详情 `/etfs/:code`**：`getEtfs`+`getOpinions`+`getSignalsHistory` 并行；最新信号卡片、数据缺失琥珀横幅、`failed_rules` 含 missing 提示、意见列表、历史信号表。
- **系统 SystemStatus `/system`**：由 overview+etfs 派生（API 连接 / 数据新鲜度 / 市场风险 / 宽度数据源 / 策略版本 / ETF 覆盖）；注明完整系统端点待 P8。

### 关键约定
- **30s 轮询范围**：仅 `overview` + `signals/latest`（DESIGN §7 指定）；`etfs`/`opinions` 由页面按需自拉并监听 `lastUpdated` 刷新。
- **Sector / System 为派生视图**：P4 实际只落地 6 个只读端点（无 `/api/sectors/*`、`/api/system/*`），前端据 `etfs` 的 `category`+`related_sector_codes` / overview 派生，UI 已明确标注，非实时排行。
- **观察期空数据兜底**：初始库 mapping/signal 为空时各端点返回 `[]`/`null`，前端 Empty/Error 三态正确渲染（已用空库验证）。
- **置信度单位**：后端 `confidence` 是 0–100 整数（curl 实测 `55.0`），前端直接展示百分比，不乘 100。
- **红涨绿跌**：所有涨跌幅、指数涨跌、宽度、信号风险图配色遵循 A股惯例，不随系统主题反转。

### 验证（本轮已跑通）
```bash
cd /workspace/frontend
npm install                       # 装 vue/echarts/vue-router + dev 工具链
npm run build                     # vue-tsc -b && vite build → 620 模块，类型检查通过
# 运行冒烟：后端 API(:8000) + vite dev(:5173, /api 代理) 起好后，用 Playwright 截 5 路由
node shot.mjs                     # 收集 window 错误 → PAGE_ERRORS: none（无运行时 JS 报错）
```
- **数据契约 1:1**：curl 6 端点（空库→Empty 兜底；seed 16 映射 + run_evaluate 后）JSON 字段与 `src/api/types.ts` 逐字段吻合。
- **构建产物**：`index 32.83KB / vue 92.09KB / echarts 1.03MB`（gzip 343KB，echarts 已单独拆 chunk，首屏不阻塞）。
- **类型检查**：`vue-tsc -b` 通过（修复了 `import.meta.env`、`node:url` 两处类型错误）。

> 说明：沙箱当前无法以图像方式肉眼校验渲染（Read 工具对 PNG 返回过滤），故以「Playwright 无 JS 错误 + curl 数据契约 + TS 类型检查」三重替代视觉验证；请你在服务器 `npm run dev` 自测视觉效果。

### 已知限制（P5）
1. **Sector / System 为派生视图**：非 P4 实时板块排行/系统端点，待 P8 补齐对应只读接口后前端直连。
2. **echarts 体积**：单 chunk ~1MB（gzip 343KB），已拆独立 chunk 并行加载；若需更低首屏可后续按需引入 echarts 子模块（P8 优化项）。
3. **观察期空数据**：mapping/信号为空时页面大量 Empty，属预期（种子数据后正常）。
4. **无单测**：前端未引入 Vitest（P5 以类型检查 + 构建 + 运行冒烟替代）；如需可 P6 补组件测试。
5. **视觉校验受限**：沙箱无法读图，渲染正确性以你服务器自测为准。

### 真机自测步骤（用户服务器）
```bash
cd /workspace && git pull                       # 拉取 P5 前端
cd /workspace/frontend
npm install                                    # 安装依赖（含 package-lock.json 锁定）
npm run build                                   # 类型检查 + 打包到 dist/（P8 由 Nginx 静态托管）
# 或本地开发预览：
npm run dev                                     # vite :5173，/api 代理到 :8000（需后端 API 已起）
# 生产：P8 用 Nginx 反代 /api/* 到 :8000 + Basic Auth + HTTPS，前端 dist/ 同源静态托管
```
> 依赖：`frontend` 与 `backend` 相互独立；前端不依赖 pandas/akshare，仅 Node 工具链。后端 API 需先按 P4 起在 :8000（P8 前可用 `python3.11 -m uvicorn app.main:app --host 127.0.0.1 --port 8000`）。
> 注意：P5 运行冒烟时沙箱对 `data/etf_monitor.db` 执行过 `seed_mapping`（16 映射）+ `run_evaluate --phase post_close`（16 信号/16 意见，幂等），属项目自带脚本，属副作用已如实记录。

### 下一步：P6（持仓分析）
- 基于 P5 详情页 + P4 信号数据，叠加持仓成本/盈亏/信号匹配（需 `positions` 数据源，待定）。
- P7 回测、P8 nginx 部署（Basic Auth + HTTPS + 反代 `/api/*` + 前端静态托管）。

---

## P6 — 按需持仓分析（已完成，2026-07-20）

> 状态：后端 analyzer + 无状态端点 + 11 项测试全绿（pytest 共 122 passed）；前端 `/portfolio` 页 + 表单/结果组件构建通过（626 模块），Playwright 冒烟 `PAGE_ERRORS: none`。
> 设计铁律（DESIGN § 按需持仓分析 / §9.5）：**无状态、默认不落库**；不保存持仓、不产生用户状态、仅主动调用时计算。P9 用户系统（保存个人持仓）明确暂缓。

### 端点契约（DESIGN §10 P6）
- `POST /api/portfolio/analyze`：提交持仓即时计算，默认不落库。
- 请求：`{ positions: [{ etf_code, cost_price>0, position_percent∈[0,100], quantity? }] }`（最多 20 只、不重复、合计 ≤ 100）。
- 返回每项：`action`(HOLD/REDUCE/EXIT/RECONFIRM) / `reason` / `risk` / `return_percent` / `pnl_amount` / `suggested_position_text` / `suggested_position_range` / `invalidation_conditions`(中文列表) / `review_time`。

### 交付文件
| 文件 | 职责 |
|---|---|
| `app/portfolio/__init__.py` | 包标识 |
| `app/portfolio/analyzer.py` | **纯函数** `analyze_portfolio(positions, session)`：复用 `signal_repo`/`quote_repo`（只读）；按 §9.5 推导动作；算 `return_percent`/`pnl_amount`；`invalidation_conditions`(bool 字典)→中文触发项列表。**不写库** |
| `app/api/schemas.py` | `PortfolioPosition`/`PortfolioAnalyzeRequest`/`PortfolioAnalyzeItem`/`PortfolioAnalyzeResponse`（含 Pydantic 基础校验） |
| `app/api/routers/portfolio.py` | `POST /api/portfolio/analyze`：**服务端强校验**（≤20 / 不重复 / cost_price>0 / position_percent∈[0,100] / 合计≤100 / 仅 `etf_mapping` 白名单）；调用 analyzer；全只读 |
| `app/api/routers/__init__.py` | 导出 `portfolio_router` |
| `app/main.py` | 挂载 `portfolio_router`（沿用只读引擎） |
| `tests/conftest.py` | 新增 `api_client_quote` fixture（播种 510300 ETF 最新 SNAPSHOT，供盈亏测试；原 fixture 默认不变） |
| `tests/test_api_portfolio.py` | 11 用例：合法分析（得分下降→REDUCE / NO_PARTICIPATE→RECONFIRM / 无信号→RECONFIRM）、白名单拒绝、重复、合计>100、cost≤0、空、超 20、带行情盈亏、无数量仅收益率 |
| `frontend/src/api/client.ts` | 新增 `apiPost<T>`（POST 封装，统一错误） |
| `frontend/src/api/types.ts` | `PortfolioAction` / `PortfolioPosition` / `PortfolioAnalyzeRequest` / `PortfolioAnalyzeItem` / `PortfolioAnalyzeResponse` |
| `frontend/src/api/endpoints.ts` | `analyzePortfolio(positions)` |
| `frontend/src/lib/tier.ts` | `ACTION_TEXT` / `ACTION_BADGE`（继续持有/降低仓位/触发退出/等待确认，静态色类防 JIT 失效） |
| `frontend/src/components/sections/PortfolioForm.vue` | 持仓录入：可增删行、客户端校验（空码/重复/成本>0/仓位 0–100/合计≤100/≤20），提交 emit 合法 positions |
| `frontend/src/components/sections/PortfolioResults.vue` | 结果卡片：动作徽标 + 收益率/盈亏（红涨绿跌）+ 建议仓位/区间 + 理由/风险 + 失效条件（琥珀横幅） |
| `frontend/src/views/PortfolioView.vue` | `/portfolio` 页：说明无状态不保存；表单 `:key` 随 `?etf=` 重挂实现预填；StatePanel 四态 |
| `frontend/src/router/index.ts` | 新增 `/portfolio` 路由（hash 模式） |
| `frontend/src/components/ui/AppNav.vue` | 导航加「持仓」 |
| `frontend/src/views/EtfDetail.vue` | 头部加「在持仓分析中查看」→ `/portfolio?etf=<code>`（落实 DESIGN 「EtfDetail 内嵌持仓分析」意图，用跳转预填而非弹窗） |

### 动作决策（§9.5，确定性）
优先级 **EXIT > REDUCE > RECONFIRM > HOLD**：
- **EXIT**（触发退出条件）：`risk_flags.veto` 或 `market_regime==BEAR` 或 ETF 相对强弱转负（`etf_rs_20d<1.0`）或 `close_below_ma20`。
- **REDUCE**（降低仓位）：`risk_flags.downgrade` 或 综合分较前一日下降 ≥5 或 档位 `NO_CHASE_HIGH`。
- **RECONFIRM**（等待重新确认）：档位 `NO_PARTICIPATE`/`OBSERVE` 或 `failed_rules` 非空（数据不全）或 `market_regime==WEAK` 或 信号超 `STALE_THRESHOLD_DAYS`(=5)。
- **HOLD**（继续持有）：其余。

### 关键约定
- **无状态**：analyzer 仅 `SELECT`，绝不写库；每次提交即时重算（DESIGN §：「不保存持仓；不产生用户状态」）。
- **盈亏来源**：当前价取 `quote_repo.get_latest_quote(session, "ETF", etf_code).close`（注意规范 `symbol_type` 为大写 `"ETF"`）；无 ETF 最新行情则 `return_percent`/`pnl_amount` 降级为 `null`（D4 缺失不崩溃）。
- **失效条件可读化**：存储为 bool 字典，返回时转中文触发项列表（仅 True 项），前端直接展示。
- **白名单**：仅 `etf_mapping` 内 ETF 可分析，防止任意代码进入计算。

### 验证（本轮已跑通）
```bash
cd /workspace/backend && python3.11 -m pytest -q        # 122 passed（P5 111 + P6 11）
cd /workspace/frontend && npm run build                  # vue-tsc -b && vite build → 626 模块，类型检查通过
# 运行冒烟（API:8000 + vite dev:5173，/api 代理）：
# Playwright 访问 /#/portfolio?etf=510300 → 预填生效、提交后结果渲染、PAGE_ERRORS: none
curl -X POST :8000/api/portfolio/analyze -d '{"positions":[{"etf_code":"510300","cost_price":3.82,"position_percent":30,"quantity":10000}]}'
# 真实库返回：action=RECONFIRM（510300 实际信号 NO_PARTICIPATE）、suggested「不新增」、
# invalidation「数据不完整」、return_percent=null（沙箱无 ETF 实时行情）——符合预期。
```
- **踩坑修复**：analyzer 初版用 `get_latest_quote(session,"etf",...)`（小写）查不到行情 → 修正为规范大写 `"ETF"`，盈亏计算恢复。
- **构建产物**：`index 41.48KB / vue 92.09KB / echarts 1.03MB(gzip 343KB)`。

### 已知限制（P6）
1. **不持久化（设计如此）**：刷新/重开需重新录入；个人持仓长期保存属 P9（用户系统），明确暂缓。
2. **盈亏依赖 ETF 实时行情**：采集未跑则 `return_percent`/`pnl_amount` 为 `null`，仅给动作与建议仓位（前端已做「无行情」占位）。
3. **动作阈值是常量**：`score_drop≥5`、`STALE_THRESHOLD_DAYS=5` 写在 `analyzer.py`，后续如需按环境调，可迁到 `settings.yaml`（未动配置以保持 P6 聚焦）。
4. **视觉校验受限**：沙箱无法读图，渲染正确性以你服务器 `npm run dev` 自测为准（已用 Playwright 无错 + curl 真实数据双重替代）。

### 真机自测步骤（用户服务器）
```bash
cd /workspace && git pull
cd /workspace/backend && python3.11 -m pytest -q        # 应 122 passed
cd /workspace/frontend && npm run build                  # 类型检查 + 打包
# 或本地预览（需后端 API 已起在 :8000）：
npm run dev                                              # :5173，访问 /#/portfolio
```
> 持仓分析需后端有信号（`run_evaluate` 已产出）与 ETF 最新行情（采集运行后）才能算盈亏；否则仅返回动作与建议仓位。

### 下一步：P7（回测）
- 回测引擎（异步，Worker 执行）：基于历史 BAR 与信号，输出策略表现（样本内/外分离，DESIGN R5）。
- P8 nginx 部署（Basic Auth + HTTPS + 反代 `/api/*` + 前端静态托管）收尾上线。

## P7 — 日线回测引擎（已完成，2026-07-20）

> 状态：后端 `backtest_engine` + 新增 2 张回测表（P7）+ 异步 `POST /api/backtest/run` 与 `GET /api/backtest/{id}`、`GET /api/backtest/runs`；**12 项测试全绿**（pytest 共 134 passed，P6 122 + P7 12）；Worker `run_backtest` 任务已注册（收盘后 15:40）。
> 设计铁律（DESIGN §10 / R4 / R5 / R8 / R9）：**复用冻结规则引擎**（不复制规则逻辑）、**无未来数据**（信号日次根开盘成交）、**样本内/外分离**、**前复权假设**、**涨跌停/停牌约束**。

### 端点契约（DESIGN §10 P7）
- `POST /api/backtest/run`（202）：仅建 PENDING 任务立即返回 `id`；**不同步执行**；盘中默认拒重型回测（`intraday_heavy_disabled` + `is_trading_now`）→ 409 `BACKTEST_INTRADAY_BLOCKED`。校验：日期合法 `start<end`、ETF 在 `etf_mapping` 白名单、`strategy_version` 必须已注册（白名单，不可现场编造）。
- `GET /api/backtest/{id}`：查进度（`status`/`progress`）+ 完成后返回 `results`（指标 / 交易 / 净值曲线）。不存在 → 404。
- `GET /api/backtest/runs`：回测任务列表（降序，分页）。
- 实际执行：Worker `run_backtest`（15:40 或手动）扫描 PENDING 逐条跑，状态机 `PENDING→RUNNING→DONE/FAILED`。

### 交付文件
| 文件 | 职责 |
|---|---|
| `app/db/models/backtest.py` | **P7 两张表** `BacktestRun`(状态机/参数/结果) + `BacktestTrade`(逐笔，含 `sample` IN/OUT) |
| `app/db/models/__init__.py` | 注册 `BacktestRun`/`BacktestTrade` 到 `Base.metadata` |
| `app/backtest_engine/backtester.py` | **纯计算** `_compute_backtest`：按交易日循环调 `StrategyEngine.evaluate_etf` → 立场；次根开盘成交(R4)；涨停不买/跌停不卖/停牌跳过(R9)；佣金+滑点；样本内/外分离(R5)；指标+净值曲线+基准买入持有 |
| `app/backtest_engine/runner.py` | `run_backtest`(状态机编排 PENDING→DONE/FAILED，写交易+results_json) + `process_pending_backtests`(Worker 入口) |
| `app/repository/backtest_repo.py` | `create_run`/`get_run`/`list_runs`/`save_trades`/`set_progress`（写库） |
| `app/api/schemas.py` | `BacktestRunRequest` / `BacktestRunOut` / `BacktestTradeOut` / `BacktestResultOut` / `BacktestRunsList` |
| `app/api/routers/backtest.py` | 上述 3 端点；`/runs` 必须在 `/{run_id}` 前注册避免被路径参数捕获 |
| `app/api/deps.py` | 新增 `build_write_engine` + `get_backtest_db`（**仅回测路由用的可写引擎**；默认查询仍走只读引擎 `query_only=ON`，DESIGN §0） |
| `app/main.py` | lifespan 创建 `backtest_engine`/`backtest_db_factory` 并挂载路由 |
| `app/api/routers/__init__.py` | 导出 `backtest_router` |
| `app/worker.py` | `job_run_backtest` + 调度器注册（15:40 收盘后） |
| `tests/conftest.py` | 新增 `_seed_backtest` + fixtures `backtest_db`/`backtest_client`（合成 260 交易日 ETF/指数/宽度 BAR，含样本内/外各一段行情） |
| `tests/test_backtest_engine.py` | 6 用例：跑通出交易 / R5 样本分离 / R4 次根开盘无未来数据 / 数据不足失败 / R9 涨停不买 / 基准对比 |
| `tests/test_api_backtest.py` | 6 用例：建 PENDING(202) / 盘中拒(409) / 校验(422×4) / 全链路 PENDING→DONE / 列表 / 未知 404 |

### 关键设计决策
- **复用冻结引擎**：回测按日调 `StrategyEngine.evaluate_etf(session, mapping, version, as_of)`，从不复制规则逻辑 → 与 DESIGN §9 冻结引擎完全一致（LLM 只润色不判断）。
- **R4 无未来数据**：信号基于截至 `as_of` 当日窗口；成交价取**信号日次根 BAR 开盘**。停牌（无 BAR）天然被「下一可用 BAR」跳过。
- **R5 样本内/外分离**：交易日序列按 `in_sample_end`（缺省 70/30）切分，分别算 `total_return/annualized/max_drawdown/sharpe/win_rate`；每笔交易按入场日标 `IN`/`OUT`；FULL = IN+OUT 聚合。
- **R8 前复权**：回测价格使用前复权 BAR（回填默认前复权）；实时展示用不复权，靠 `data_kind` 区分。
- **R9 涨跌停/停牌**：次根 `close≥前收×1.099` 涨停 → 不买；`≤前收×0.901` 跌停 → 不卖；无 BAR → 跳过。
- **成本模型**：佣金 `commission_per_thousand/1000` + 滑点 `slippage_bps/10000`，双边；`BacktestConfig` 可调（默认 510300 基准）。
- **立场模型（MVP）**：单 ETF 多头/空仓（long/flat），`OPPORTUNITY_ENHANCE`/`SMALL_POSITION`→满仓，其余→空仓。更细仓位区间（DESIGN §9.6）为后续增强，**不影响信号正确性**。
- **API 只读 + 回测写**：默认查询端点仍走 `query_only=ON` 只读引擎；回测建任务用独立可写引擎（仅此路由），与 Worker 写同一 SQLite(WAL)，靠 `busy_timeout` 串行化。

### 验证（本轮已跑通）
```bash
cd /workspace/backend && python3.11 -m pytest -q        # 134 passed（P6 122 + P7 12）
# 引擎冒烟（合成 260 日数据）：full 19 笔交易，样本内 9 / 样本外 10；
#   full 收益 5.71% / 夏普 2.52；基准买入持有 162%（本策略为 long/flat，熊市空仓属预期）
curl -X POST :8000/api/backtest/run -d '{"etf_code":"510300","start_date":"2024-01-01","end_date":"2024-12-27"}'  # 202 + id
curl :8000/api/backtest/{id}                            # Worker 跑完后返回 status=DONE + results
```

### 已知限制（P7）
1. **立场模型为 long/flat**：未实现 DESIGN §9.6 数值仓位区间（如 25–50% 半仓）；属后续增强，不影响信号/指标正确性。
2. **前复权假设**：回测直接吃存储的 BAR 视为前复权；若线上 BAR 混用不复权需在建表/回填层保证（DESIGN §0 已约定回填前复权）。
3. **逐日调引擎（R6 优化项）**：MVP 每日重查 DB（小样本足够）；生产多年数据可改为「批量预读 BAR + 内存计算」以降 CPU/内存，逻辑不变。
4. **沙箱无真实历史数据**：AkShare em 不可达，无法跑真实库端到端；引擎以合成数据单测 + 接口全链路验证（Worker 执行器在测试中模拟）。
5. **无前端回测页**：P7 仅后端引擎 + API；前端可视化（净值曲线/样本内外对比）属后续（DESIGN §复杂回测页 第二阶段）。

### 真机自测步骤（用户服务器）
```bash
cd /workspace && git pull
cd /workspace/backend && python3.11 -m pytest -q        # 应 134 passed
# 手动触发一次回测（绕过盘中限制可在收盘后，或临时将 settings.backtest.intraday_heavy_disabled 置 false）：
curl -X POST :8000/api/backtest/run -d '{"etf_code":"510300","start_date":"2024-01-01","end_date":"2024-12-27"}'
# Worker 15:40 自动执行；或手动起 worker 后查看：
curl :8000/api/backtest/runs
curl :8000/api/backtest/{id}
```

### 下一步：P8（部署）
- nginx（Basic Auth + HTTPS）+ `etf-api`(1 worker)/`etf-worker` 进程分离 + `db_backup` 脚本（`.backup` 本地 7 天 + 周传异地）。
- 前端静态托管；回测结果可视化为后续（第二阶段复杂回测页）。


---

## 轮次 P8 — 部署与备份（nginx + systemd + db_backup，2026-07-20）

**状态**：部署产物已落地（nginx.conf / 两个 systemd unit / ops.md）；`db_backup.py` 已在沙箱实测跑通（产出 172K 压缩备）。**真机启用需用户在服务器执行 nginx 安装、证书申请、防火墙与 systemctl**（见 `docs/ops.md`）。

### 本轮交付清单（文件 → 职责）
| 文件 | 职责 | 关键设计 |
|---|---|---|
| `deploy/nginx.conf` | 站点配置：Basic Auth + HTTPS + 反代 `/api/*` + 静态托管前端 | 80→443 跳转仅留 ACME 挑战；API 反代保留 `/api` 前缀；`/health`/`/ready`/`/docs` 关闭 Basic Auth 便于探测 |
| `deploy/etf-api.service` | systemd 单元：uvicorn **1 worker**，监听 127.0.0.1:8000 | 单 worker 为 DESIGN §0 硬性要求；`WorkingDirectory=/workspace/backend` 才能 `import app` |
| `deploy/etf-worker.service` | systemd 单元：APScheduler 单实例（fcntl 锁） | 承载采集/评估/回测/备份/清理；与 api 共享 WAL SQLite |
| `docs/ops.md` | 部署与运维手册 | 含 venv/前端 build/nginx/certbot/systemd/验证/备份/升级全流程 |
| `backend/scripts/db_backup.py` | 本地日备（已存在，本轮实测） | `sqlite3.backup()` + gzip + 7 天保留；异地周备为占位 hook（`backup_remote_enabled=false`） |

### 关键决策
- **P8 归属 DevOps**：无前端新页面（前端 P5 已完成，仅 `npm run build` 交 nginx 托管）；回测曲线可视化属第二阶段，不在 P8。
- **端口隔离**：API 仅监听回环，防火墙封 8000，公网只暴露 443（反代）。符合 DESIGN §0。
- **鉴权唯一层在 nginx Basic Auth**：FastAPI 无鉴权层（DESIGN §0），运维口令即访问口令。
- **迁移手动运行 → systemd**：之前手动 `python -m app.worker` 的用户，需先 `pkill` 释放 `.etf_worker.lock` 再启用服务。
- **部署脚本全部入库可 push**：用户在服务器执行安装/证书/防火墙/起服；agent 仅产出模板与逐条命令。

### 验证（本轮已跑通）
```bash
cd /workspace/backend && python3.11 -m scripts.db_backup   # 产出 data/backups/etf_monitor_YYYYMMDD.db.gz
# db_backup 由 worker 每天 02:00 db_backup 任务自动调用（无需手工 cron）
```

### 真机自测步骤（用户服务器）
```bash
# 按 docs/ops.md §0–§6 依次执行：拉代码 → venv → 前端 build → 配 .env → nginx + certbot → systemd
systemctl status etf-api etf-worker
curl -sS https://jiucaietf.icu/health
curl -sS -u admin:密码 https://jiucaietf.icu/api/market/overview
# 浏览器打开 https://jiucaietf.icu/ → 输入 Basic Auth → 见总览页
```

### 已知限制（P8）
1. **异地周备未启**：`backup_remote_enabled=false`，待对象存储/rclone 就绪后接 `db_backup._upload_remote`。
2. **Basic Auth 无审计**：内部 10 人够用；按人审计/撤销属 P9 用户系统。
3. **nginx 真机配置未在沙箱验证**：沙箱无 nginx；配置为标准写法，启用前以 `nginx -t` 校验。
4. **venv 路径假设**：systemd unit 假设 venv 在 `/workspace/backend/venv`；若不同请同步改 `ExecStart`。

### 下一步
- 用户在服务器按 `docs/ops.md` 落地并自测（每阶段完成即暂停自测，符合约定）。
- 可选：P9 用户系统（仅当需要保存个人持仓/历史建议时）；回测结果前端可视化（第二阶段）。

### P8 补丁：overview 指数回退 SNAPSHOT（2026-07-21）
- **背景**：`/api/market/overview` 原只查指数日线 BAR（`data_kind=BAR`）显示 close/change；但 em 历史回填在用户云服务器失败（DESIGN 已预言：em-only 历史在沙箱/用户服务器会失败），INDEX BAR 为空 → 指数恒为 null。
- **改动**：`app/api/routers/market.py` 的 overview 在 BAR 缺失时回退查最新 `SNAPSHOT`（collect_once 已存实时 close）。优先 BAR、回退 SNAPSHOT，纯展示层兜底，**不动冻结的策略引擎**。
- **效果**：指数当下即显示实时值（盘中随快照刷新）；有日线 BAR 后仍优先用 BAR。
- **已知**：图表/信号仍需价格历史。em 回填失败环境下，信号为"无数据型"（MARKET_RISK_HIGH/NO_PARTICIPATE，D4 优雅降级，符合预期）；价格历史需等交易时段 worker 累积或 em 可用后变丰富。
