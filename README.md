# CJETF —— A股板块资金流监控 · ETF 交易参考

监控 A股**板块资金流向 + 涨跌停 + 实时行情**，输出 ETF 交易参考意见的网站。
面向 ≤10 人的内部小团队，部署在腾讯云 CVM（4 核 / 4G / 60G 系统盘）。

> 完整运维手册见 [`docs/ops.md`](docs/ops.md)；agent 交接速读见 [`docs/HANDOFF.md`](docs/HANDOFF.md)；
> 设计系统规范（配色 / 排版 / 间距 / 阴影）见 [`DESIGN.md`](DESIGN.md)。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11 + FastAPI + SQLAlchemy + SQLite(WAL) + Pydantic |
| 前端 | Vue 3.4 + Vite 5 + TypeScript + Tailwind v3.4 + ECharts 5（hash 路由） |
| 部署 | Nginx（Basic Auth + HTTPS，鉴权在 Nginx，后端无鉴权层）反代 FastAPI；systemd 托管 `etf-api` / `etf-worker` |

- API 仅监听 `127.0.0.1:8000`（端口隔离），由 Nginx 反代，**不对外暴露 8000**。
- 采集 / 信号评估 / 回测 / 备份 / 清理全部跑在 `etf-worker` 单实例（fcntl 文件锁防重复写库）。

---

## 目录结构

```
backend/        后端（FastAPI 应用、采集器、评估引擎、tests/）
frontend/       前端（Vue3 + Vite，构建产出 frontend/dist/）
config/         配置（settings.yaml 入库；.env 不入库，承载敏感覆盖）
deploy/         systemd 单元 + nginx 站点配置（生产用）
docs/           devlog.md（开发日志）/ HANDOFF.md（交接）/ ops.md（运维手册）
DESIGN.md       设计系统规范（9 章节）
```

---

## 快速开始（本地开发 / 沙箱）

```bash
# 后端：venv + 跑测试
cd backend
python3.11 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python -m pytest -q

# 前端：Node ≥18，pnpm 务必用 9.x（pnpm@latest 需 Node ≥22.13，Node 20 跑不了）
cd frontend
pnpm install --frozen-lockfile
pnpm build                 # 产出 dist/
pnpm dev                   # 本地开发服务（可选）
```

---

## 部署到生产服务器（腾讯云 CVM · Ubuntu 22.04）

### 0. 前置确认

- [ ] 系统 Ubuntu 22.04+，有 `sudo` 权限；Python 3.11+；Node 20 LTS（**不要用 Node 16/22**）。
- [ ] 防火墙放行 `22` / `80` / `443`；**不要**放行 `8000`（API 仅回环）。
- [ ] （HTTPS 用）域名 `A 记录`已解析到本机公网 IP（裸 IP 只能走 §5 的纯 HTTP 临时方案）。

### 1. 拉代码 + 装依赖

```bash
cd /workspace
git clone https://github.com/DingzhenBOT/jcetf.git .     # 或已在 /workspace：git pull origin main

# 后端 venv + 依赖
cd /workspace/backend
python3.11 -m venv venv
./venv/bin/python -m pip install -U pip
./venv/bin/python -m pip install -r requirements.txt

# 前端：必须 pnpm 9（Node 20 环境）
#   sudo corepack disable; sudo npm i -g pnpm@9
cd /workspace/frontend
pnpm install
pnpm build                 # 产出 frontend/dist，由 nginx 托管
```

> ⚠️ 前端依赖用 **pnpm 9.x**。仓库已标准化 `pnpm-lock.yaml`（lockfileVersion 9.0），
> 移除过 npm 的 `package-lock.json`。`pnpm@latest`（v10）要求 Node ≥22.13 + `node:sqlite`，
> 在 Node 20 上会直接崩（`ERR_UNKNOWN_BUILTIN_MODULE: node:sqlite`），务必 `npm i -g pnpm@9`。
>
> 安装后若仍报 `/usr/local/bin/pnpm: No such file or directory`：先 `sudo corepack disable` 清掉
> 旧的 corepack 软链，再 `sudo npm install -g pnpm@9`，然后 **`hash -r`** 刷新 shell 命令缓存。
> 仍不行可用 corepack 直接钉版本：`sudo corepack prepare pnpm@9.15.9 --activate && hash -r`。

### 2. 运行配置

```bash
cp /workspace/config/.env.example /workspace/config/.env
# 按需编辑：ETF_ENV=prod、ETF_API_HOST=127.0.0.1、ETF_API_PORT=8000 等
```

主配置 `config/settings.yaml` 已入库、可直接用（API 默认监听 `127.0.0.1:8000`）。

### 3. 数据库初始化（首次）

```bash
cd /workspace/backend
./venv/bin/python -m scripts.init_db        # 建表 + 索引
./venv/bin/python -m scripts.seed_mapping   # 种子 ETF→板块映射（幂等；不跑则评估无对象、无信号）
# 想立刻看到指数 close/change 与信号/风险，再跑一次评估（含历史回填）：
./venv/bin/python -m scripts.run_evaluate --phase post_close --backfill
#   --backfill 回填历史日线 BAR（走东方财富；不可达则非致命，等交易时段自动累积）
```

之后由 `etf-worker` 在交易时段自动采集 + 评估，无需手动。

### 3.5 盈米 CLI 初始化（场外基金真实数据）

「场外基金（盈米）」实时数据依赖 `yingmi-skill-cli` 的本地授权 `apiKey`。**该 CLI 未在 CVM 安装/授权时会报
`yingmi-skill-cli 未安装或未授权`**，场外数据接口返回 `available:false` 降级（不影响场内 ETF 主流程）。

首次部署需在 CVM 上完成以下步骤（交互式手机号 + 短信验证码只能由你本人操作，agent 无法代填）：

```bash
# ① 检查 CLI 是否已装且为最新（未装则进入安装）
yingmi-skill-cli --version
yingmi-skill-cli upgrade --check-only
#   → 若命令不存在 / 非最新，安装（权限不足加 sudo）：
sudo npm install -g yingmi-skill-cli@latest --registry=https://registry.npmmirror.com --prefer-online

# ② 检查初始化状态
yingmi-skill-cli init status
#   → hasApiKey: true 即已完成，可直接跳过后续；否则继续。

# ③ 查看初始化引导（仅未初始化时需要）
yingmi-skill-cli init setup

# ④ 用你的手机号发送验证码（把 <手机号> 换成真实号码）
yingmi-skill-cli init setup --phone <手机号>

# ⑤ 查收短信，用验证码完成初始化（自动写入 apiKey）
yingmi-skill-cli init setup --verify-code <验证码>

# ⑥ 复核：hasApiKey 应为 true
yingmi-skill-cli init status
#   排查：yingmi-skill-cli init doctor   # 返回 status:"ok" 且 api-key:ok 即链路正常
```

完成授权后，`/api/external/off-exchange` 才会返回 `available:true` 的真实场外基金数据。

> 这是**一次性**初始化；`apiKey` 写入 `~/.yingmi-skill-cli/config.json`，重启/升级代码不受影响。
> 重新部署后若场外数据变 `available:false`，先重跑 ② 复核，必要时重跑 ④⑤。

> **⚠️ 常见坑：root / ubuntu 的 $HOME 不一致导致「报未初始化」**
> 后端以 root 运行（`deploy/etf-api.service: User=root`），而初始化常在 `ubuntu` 用户下完成，
> 于是服务端调用 `yingmi-skill-cli` 时 `$HOME=/root`，读 `/root/.yingmi-skill-cli/config.json`
> 找不到授权，CLI 报：`未完成初始化，请先执行: yingmi-skill-cli init setup --phone <手机号>`；
> 但你在自己 ubuntu shell 跑 `init status` 却显示已初始化（读的是 `/home/ubuntu/.yingmi-skill-cli`）。
> 三种解法任选其一：
> 1. **（推荐，无需重发验证码）** 在 `/workspace/config/.env` 增加一行 `YINGMI_HOME=/home/ubuntu`
>    （systemd `EnvironmentFile` 会注入到后端进程环境，盈米子进程据此读取 ubuntu 的授权文件），
>    然后 `sudo systemctl restart etf-api`。
> 2. **软链**：`sudo ln -sfn /home/ubuntu/.yingmi-skill-cli /root/.yingmi-skill-cli`，再重启服务。
> 3. **以 root 重做初始化**（需再收一次短信验证码）：`sudo su -` → `yingmi-skill-cli init setup --phone <手机号>`
>    → `yingmi-skill-cli init setup --verify-code <验证码>` → `exit`，再重启服务。
> 代码层已支持 `YINGMI_HOME`：`app/services/external_data.py` 的 `_yingmi_env()` 会在该变量存在时
> 覆盖子进程的 `HOME`。

### 4. 进程托管：systemd

```bash
sudo cp /workspace/deploy/etf-api.service    /etc/systemd/system/
sudo cp /workspace/deploy/etf-worker.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now etf-api
sudo systemctl enable --now etf-worker

# 状态 / 日志
systemctl status etf-api etf-worker
journalctl -u etf-api -u etf-worker -f
```

> 之前若用手动 `python -m app.worker` 跑过，先 `pkill -f app.worker` 释放锁文件再启用服务。

### 5. 反向代理 + HTTPS：nginx

**无域名临时方案（仅 IP / 纯 HTTP，先看效果）：**

```bash
sudo cp /workspace/deploy/nginx.http.conf /etc/nginx/sites-available/jcetf
sudo ln -sf /etc/nginx/sites-available/jcetf /etc/nginx/sites-enabled/jcetf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx
# 浏览器打开：http://<你的服务器公网IP>/
```

**已有域名 + HTTPS：**

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx apache2-utils

sudo cp /workspace/deploy/nginx.conf /etc/nginx/sites-available/jcetf
sudo ln -sf /etc/nginx/sites-available/jcetf /etc/nginx/sites-enabled/jcetf
sudo rm -f /etc/nginx/sites-enabled/default

# Basic Auth 口令文件（可加多个用户）
sudo htpasswd -c /etc/nginx/.htpasswd_jcetf admin    # 按提示设密码

# 申请 Let's Encrypt 证书（需 80 端口可达 + 域名已解析）
sudo certbot certonly --webroot -w /var/www/letsencrypt -d jiucaietf.icu -d www.jiucaietf.icu

sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
# （可选）续期测试：sudo certbot renew --dry-run
```

> nginx.conf 中 `server_name` 与证书路径默认 `jiucaietf.icu`；域名不同请同步修改。
> 腾讯云等云主机还需在**安全组**放行入站 80 端口，否则公网打不开。

### 6. 验证

```bash
# 纯 HTTP / IP
curl -sS http://<公网IP>/health
curl -sS -u admin:密码 http://<公网IP>/api/market/overview | head

# HTTPS
curl -sS https://jiucaietf.icu/health
curl -sS -u admin:密码 https://jiucaietf.icu/api/market/overview | head
```

预期：`/health` 返回 `{"status":"ok"}`；`/api/market/overview` 返回 JSON；浏览器首页可加载（输入 Basic Auth 后）。

### 7. 升级流程

```bash
cd /workspace && git pull origin main
cd backend && ./venv/bin/python -m pip install -r requirements.txt && cd ..
cd frontend && pnpm install && pnpm build && cd ..
sudo systemctl restart etf-api etf-worker
# nginx 配置如有变更：sudo nginx -t && sudo systemctl reload nginx
```

### 8. 数据库备份

- 本地日备已落地：`etf-worker` 每天 02:00 的 `db_backup` 任务调用 `backend/scripts/db_backup.py`
  （`sqlite3 .backup` 一致快照 → gzip 到 `data/backups/etf_monitor_YYYYMMDD.db.gz`，保留 7 天）。
- 手动：`cd /workspace/backend && ./venv/bin/python -m scripts.db_backup`

---

## 常用命令速查

```bash
# 后端测试（venv 在 backend/venv，Python 3.11）
cd /workspace/backend && ./venv/bin/python -m pytest -q

# 前端构建（Node ≥18，pnpm 9.x）
cd /workspace/frontend && pnpm install --frozen-lockfile && pnpm build

# 推送到远程（token 勿硬编码进代码，推送时用临时 URL）
git push https://<TOKEN>@github.com/DingzhenBOT/jcetf.git HEAD:main
```

---

## 已知限制

- **Basic Auth 是唯一鉴权**：内部 10 人可用；按需再补 P9 用户系统。
- **worker 单实例**：刻意设计（防重复采集/写库），故障由 systemd `Restart` 兜底。
- **异地周备未启**：待配置对象存储后接入 `db_backup._upload_remote`。
- 网络波动曾导致重复命令改坏代码：所有外部依赖失败都走 `external_data.py` 的 `available:` 降级契约，新增端点沿用 `/api/external` 的优雅降级风格。
