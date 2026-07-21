# 部署与运维手册（P8）

> 适用对象：`jiucaietf.icu` 单台 Ubuntu 服务器（4 核 4G / 60G），10 人以内内部使用。
> 架构来源：DESIGN.md §0（端口隔离 / 无 API 鉴权层）、§8（定时任务）、§10（分阶段计划/P8）。

---

## 0. 上线前置（务必先确认）

- [ ] **域名 A 记录**：`jiucaietf.icu` 与 `www.jiucaietf.icu` 已解析到本服务器公网 IP。
  （certbot 申请证书时需要 80 端口可访问，DNS 生效后再申请。）
- [ ] **系统**：Ubuntu 22.04+，有 `sudo` 权限。
- [ ] **Python**：3.11+（仓库按 3.11 验证；DESIGN 允许 3.12，venv 自行选定）。
- [ ] **Node**：18+（前端 build 用；你服务器为 v20.18.0，满足）。
- [ ] **防火墙**：放行 `22`(SSH) / `80` / `443`；**不要**放行 `8000`（API 仅回环，由 nginx 反代）。

---

## 1. 获取代码与依赖

```bash
# 1) 拉代码（首次）
cd /workspace
git clone https://github.com/DingzhenBOT/jcetf.git .
# 或已在 /workspace：git pull origin main

# 2) 后端虚拟环境 + 依赖
cd /workspace/backend
python3.11 -m venv venv
./venv/bin/python -m pip install -U pip
./venv/bin/python -m pip install -r requirements.txt

# 3) 前端构建（产出 frontend/dist，由 nginx 托管）
cd /workspace/frontend
npm install
npm run build      # 输出到 /workspace/frontend/dist
```

## 2. 运行配置

```bash
# 复制环境变量模板（.env 不入库，承载环境/敏感覆盖项）
cp /workspace/config/.env.example /workspace/config/.env
# 按需编辑：ETF_ENV=prod、ETF_API_HOST=127.0.0.1、ETF_API_PORT=8000 等
```

主配置 `config/settings.yaml` 已入库、可直接用（API 默认监听 `127.0.0.1:8000`，符合端口隔离）。

## 3. 数据库初始化（首次）

```bash
cd /workspace/backend
./venv/bin/python -m scripts.init_db        # 建表 + 索引
./venv/bin/python -m scripts.seed_mapping   # 种子 ETF→板块映射（幂等；不跑则评估无对象、无信号）
```

> **首次还要生成信号/补齐历史**：`collect_once` 只采实时快照+宽度，不写日线 BAR、不跑评估。
> 想立刻看到指数 close/change 与信号/风险，再跑：
> ```bash
> ./venv/bin/python -m scripts.run_evaluate --phase post_close --backfill
> #   --backfill 回填历史 BAR（走 em 东方财富；不可达则非致命失败，等交易时段自动累积）
> #   不跑评估则 /api/market/overview 的 signal_risk 恒为空
> ```
> 之后由 `etf-worker` 在交易时段自动采集+评估，无需手动。

## 4. 进程托管：systemd

```bash
sudo cp /workspace/deploy/etf-api.service    /etc/systemd/system/
sudo cp /workspace/deploy/etf-worker.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now etf-api
sudo systemctl enable --now etf-worker

# 查看状态 / 日志
systemctl status etf-api etf-worker
journalctl -u etf-api -u etf-worker -f
```

- `etf-api`：uvicorn 单 worker，监听 `127.0.0.1:8000`（DESIGN §0 硬性要求）。
- `etf-worker`：APScheduler 单实例（fcntl 锁），承载采集/评估/回测/备份/清理。

> 之前若用手动 `python -m app.worker` 跑过，先 `pkill -f app.worker` 释放锁文件，再启用服务。

## 5. 反向代理 + HTTPS：nginx

> ### 5.0 无域名临时方案（仅 IP / 纯 HTTP）— 先看效果用这个
> 域名还没注册时**无法申请 Let's Encrypt 证书**（LE 不给裸 IP 发证，且要求域名已解析到你服务器）。
> 此时跳到 §5.4/§5.6 会失败，属正常。请改用纯 HTTP 配置先用 IP 看效果：
> ```bash
> sudo cp /workspace/deploy/nginx.http.conf /etc/nginx/sites-available/jcetf
> sudo ln -sf /etc/nginx/sites-available/jcetf /etc/nginx/sites-enabled/jcetf
> sudo rm -f /etc/nginx/sites-enabled/default
> sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx
> # 浏览器打开： http://<你的服务器公网IP>/
> ```
> 等域名注册并解析到本机后，再 `cp deploy/nginx.conf` 覆盖、跑 §5.4 申请证书、换回 HTTPS。
> 注意：云服务器（腾讯云 CVM 等）还需在**安全组**放行入站 80 端口，否则公网打不开。

```bash
# 5.1 安装 nginx + certbot
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx

# 5.2 站点配置（反代 + 静态托管 + Basic Auth）
sudo cp /workspace/deploy/nginx.conf /etc/nginx/sites-available/jcetf
sudo ln -sf /etc/nginx/sites-available/jcetf /etc/nginx/sites-enabled/jcetf
# 若 default 站点冲突可移除：sudo rm -f /etc/nginx/sites-enabled/default

# 5.3 生成 htpasswd 口令文件（Basic Auth 用户/密码）
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd_jcetf admin    # 按提示设密码，可加多个用户

# 5.4 申请 Let's Encrypt 证书（需 80 端口可达 + 域名已解析）
sudo certbot certonly --webroot -w /var/www/letsencrypt \
    -d jiucaietf.icu -d www.jiucaietf.icu

# 5.5 校验并重载
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx

# 5.6 （可选）自动续期测试
sudo certbot renew --dry-run
```

> nginx.conf 中 `ssl_certificate` 路径默认 `/etc/letsencrypt/live/jiucaietf.icu/...`，
> 若域名不同请同步修改 `server_name` 与证书路径。

## 6. 验证

```bash
# 无域名（纯 HTTP / IP）时：
curl -sS http://<你的公网IP>/health
curl -sS -u admin:密码 http://<你的公网IP>/api/market/overview | head
# 浏览器打开： http://<你的公网IP>/   -> 输入 Basic Auth 后见总览页

# 已有域名 + HTTPS 时：
curl -sS https://jiucaietf.icu/health
curl -sS -u admin:密码 https://jiucaietf.icu/api/market/overview | head
```

预期：`/health` 返回 `{"status":"ok"}` 类；`/api/market/overview` 返回 JSON；浏览器首页可加载。

## 7. 数据库备份（db_backup 脚本）

本地日备已落地（`backend/scripts/db_backup.py`，见 DESIGN §8 / §10）：

- 使用 `sqlite3.connect().backup()`（等同 CLI `.backup`，WAL 下一致）；
- gzip 压缩到 `data/backups/etf_monitor_YYYYMMDD.db.gz`；
- 本地保留 `housekeeping.backup_retention_days`（默认 7 天）；
- **Web 自动触发**：`etf-worker` 每天 02:00 的 `db_backup` 任务会调用本脚本。

手动运行 / 调试：

```bash
cd /workspace/backend
./venv/bin/python -m scripts.db_backup
ls -lh /workspace/data/backups/
```

异地周备（DESIGN §10）：`housekeeping.backup_remote_enabled` 当前为 `false`（占位 hook）。
启用前需准备对象存储 / 异地机（rclone 或 rsync），在 `db_backup._upload_remote` 接入，
并在 `settings.yaml` 置 `backup_remote_enabled: true`。

## 8. 升级流程

```bash
cd /workspace
git pull origin main
# 后端依赖如有变更：
cd backend && ./venv/bin/python -m pip install -r requirements.txt && cd ..
# 前端如有变更：
cd frontend && npm install && npm run build && cd ..
# 重启服务
sudo systemctl restart etf-api etf-worker
# nginx 配置如有变更：sudo nginx -t && sudo systemctl reload nginx
```

## 9. 已知限制 / 下一步

- **Basic Auth 是唯一鉴权**：内部 10 人可用；如需按人审计/撤销，P9 用户系统再补。
- **worker 单实例**：刻意设计（防重复采集/写库）；故障由 systemd `Restart` 兜底。
- **异地周备未启**：待配置对象存储后接入 `db_backup._upload_remote`。
- **回测结果前端可视化**：属第二阶段，不在 P8（当前结果经 `/api/backtest/{id}` 取 JSON）。
