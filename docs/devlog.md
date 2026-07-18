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
- 上 GitHub 需要你提供：① 仓库（新建 or 已有 URL）；② 个人访问令牌（PAT，repo 权限）。提供后我加 remote 并 push；或你选择「只本地提交，push 我自己来」。

### 已知限制（P0b）
1. 清理目前只覆盖 `market_quote` 的 SNAPSHOT/BAR；opinion/signal 表清理待 P1 定列后补。
2. 异地周备未实现（P8）；`backup_remote_enabled=true` 时仅告警不静默失败。
3. 磁盘守卫只告警不处置（避免误删业务数据），处置靠保留策略 + 手动。

### 下一步
- **P1**：8 张核心表 ORM + 索引 + `init_db.py`，并落实 `timestamp` naive UTC 约束；`/ready` 接 DB ping；把 opinion/signal 清理接入 `run_retention`。
- GitHub：等你给仓库信息后 push 基线。
