#!/bin/bash
# CJETF CVM 盘中数据不收集/不更新 一键诊断脚本
# 在 CVM 上直接执行：bash diagnose_worker.sh
# 检查项：etf-worker 进程状态 / 数据库最新数据时间 / 日志错误 / 磁盘空间
set -euo pipefail

DATA_DIR="/opt/jcetf/data"        # 按实际部署路径调整
DB="$DATA_DIR/etf_monitor.db"
LOG_DIR="/opt/jcetf/logs"         # 按实际部署路径调整

echo "========================================"
echo " CJETF Worker 诊断 ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
echo "========================================"
echo ""

# 1. systemd 状态
echo "--- [1] etf-worker systemd 状态 ---"
if systemctl is-active --quiet etf-worker 2>/dev/null; then
  echo "  ✅ etf-worker 正在运行"
  systemctl status etf-worker --no-pager -l 2>/dev/null | head -12
else
  echo "  ❌ etf-worker 未运行！"
fi
echo ""

# 2. 最近 journal 日志（最近 200 行，过滤关键错误）
echo "--- [2] etf-worker 最近日志 (journalctl, 关键行) ---"
if journalctl -u etf-worker --no-pager -n 200 2>/dev/null | grep -iE "error|fail|started|stopped|job ok|job failed|trading_now|calendar" | tail -20; then
  :
else
  echo "  (无匹配或 journalctl 不可用)"
fi
echo ""

# 3. 数据库最新数据时间戳
echo "--- [3] 数据库 market_quote 最新记录 (SNAPSHOT) ---"
if [ -f "$DB" ]; then
  python3 -c "
import sqlite3, json
db = sqlite3.connect('$DB')
db.row_factory = sqlite3.Row
for row in db.execute(\"SELECT symbol_type, data_kind, timeframe, MAX(timestamp) as latest, COUNT(*) as cnt FROM market_quote WHERE data_kind='SNAPSHOT' GROUP BY symbol_type, data_kind, timeframe ORDER BY symbol_type\"):
    d = dict(row)
    print(f\"  {d['symbol_type']:12s} latest={d['latest']}  count={d['cnt']}\")
print()
for row in db.execute(\"SELECT target_etf, trading_date, generated_at, score, market_regime FROM signal ORDER BY generated_at DESC LIMIT 3\"):
    d = dict(row)
    print(f\"  signal {d['target_etf']}: trade={d['trading_date']} gen={d['generated_at']} score={d['score']} regime={d['market_regime']}\")
print()
for row in db.execute(\"SELECT COUNT(*) as cnt FROM task_run_log\"):
    print(f\"  task_run_log: {row[0]} 条\")
print()
for row in db.execute(\"SELECT COUNT(*) as cnt FROM market_breadth\"):
    print(f\"  market_breadth: {row[0]} 条\")
"
else
  echo "  ❌ 数据库文件不存在: $DB"
fi
echo ""

# 4. 磁盘空间
echo "--- [4] 磁盘空间 ---"
df -h "$DATA_DIR" 2>/dev/null || df -h /
echo ""

# 5. API 进程状态
echo "--- [5] etf-api systemd 状态 ---"
if systemctl is-active --quiet etf-api 2>/dev/null; then
  echo "  ✅ etf-api 正在运行"
else
  echo "  ❌ etf-api 未运行！"
fi
echo ""

# 6. 网络连通性 (gtimg)
echo "--- [6] 数据源连通性 (腾讯财经 gtimg) ---"
if curl -s --connect-timeout 5 "http://qt.gtimg.cn/q=sh510300" | head -1 | grep -q "510300"; then
  echo "  ✅ qt.gtimg.cn 可达 (sh510300)"
else
  echo "  ❌ qt.gtimg.cn 不可达"
fi
echo ""

echo "========================================"
echo " 诊断完成。请将以上输出反馈给 agent。"
echo "========================================"
