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
