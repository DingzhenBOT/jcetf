# CJETF 交易方法论审计文档（硬编码逻辑全表）

> 用途：把后端**所有写死（hard-coded）的交易方法论 / 交易逻辑**按「模块 + 方法论」整理，供人工审核。
> 范围：仅含确定性规则（无 LLM / 无外部推断）。每条标注 `file:line` 与精确常量，方便定位与回归。
> 最后核对：策略规则 v3.0。历史行号可能随实现变化，规则真值以当前测试与 `rules.py` 哈希版本为准。
> 配套：审计发现见文末 `A1–A10`（含严重性、影响、修复建议）；审核清单见最后。

---

## 0. 总览

| 项 | 说明 |
|---|---|
| 架构 | FastAPI + SQLAlchemy + SQLite(WAL) + Pydantic；前端 Vue3 / ECharts（hash router） |
| 决策形态 | 每支 ETF → `Signal{signal_type（档位）, score, confidence, market_regime, risk_flags, supporting_metrics, invalidation_conditions, suggested_position_range, trade_plan}` |
| 三相位 | `live`（盘中每 5min）/ `lunch`（午盘 11:40）/ `post_close`（收盘后 15:10 三档价位） |
| 方案B 场外基金 | `etf_mapping.listing='场外'` → 引擎读 `OFF_FUND` 净值当日 K，仅 `post_close` 评估，复用 ETF 引擎 |
| 确定性基调 | 评分=Σ 权重·分；缺失项权重**重归一化**（D4）；风险=降级/否决，**非**扣分清分 |
| 版本化 | `strategy_hash = SHA256(params+rules)`，旧版本不可覆盖（见 `M8`） |

> ⚠️ 关键治理缺口：**策略版本哈希只覆盖 3 个 config 字典**（见 A9），引擎内数十个硬编码常量改动不会生成新版本，审计/回放可能失真。

---

## M1. 综合评分与档位决策 — `backend/app/strategy_engine/engine.py`

### M1.1 综合评分 `compute_composite` (engine.py:76-99)
- 缺失项权重重归一化：`norm[k] = weights[k] / Σ(available)`，再 `Σ norm·available`。
- 置信度：`confidence = 100 - len(missing) * MISSING_PENALTY`，`MISSING_PENALTY = 15` (engine.py:50)。
- 全部缺失 → `composite=None`。

### M1.2 市场环境评分 `_evaluate_market` (engine.py:306-404)
`market_score` 起点 **50** (engine.py:343)，上限 `clamp[0,100]` (engine.py:395)：

| 条件 | 修正 | 行 |
|---|---|---|
| 收盘 > MA20 | +35 | 356 |
| MA20 斜率 > 0 | +15 | 358 |
| 上涨家数占比 `advance_ratio > 0.60` | +15 | 382 |
| `advance_ratio < 0.40` | −15 | 384 |
| `0.55 < advance_ratio ≤ 0.60` | +5 | 386 |
| `0.40 ≤ advance_ratio < 0.45` | −5 | 388 |
| 成交额 / 近 5 日均 `amount_ratio > 1.1` | +10 | 391 |
| `amount_ratio < 0.9` | −5 | 393 |

市场状态 `market_regime`（engine.py:363-378）：`STRONG_UP` / `TREND_UP` / `VOLATILE` / `WEAK` / `BEAR`。
盘中日线 regime 可能被实时指数修正（engine.py:400-403，`_intraday_regime` 仅当日上涨/平盘时抬升，走弱保持日线判断）。

### M1.3 ETF 相对强弱评分 (engine.py:487-489)
```
etf_rs_score = clamp(0,100, 50 + (rs_20d - 1) * 100)
```
`rolling_rs` 使用共同交易日对齐后的 `n+1` 个收盘价计算增长倍数之比，停牌或数据源缺日不会按位置错配。

### M1.4 盘中动量加性修正 `intraday_momentum_adjustment` (engine.py:109-126)
常量（engine.py:103-106）：

| 常量 | 值 | 含义 |
|---|---|---|
| `INTRADAY_ADJ_MAX` | 18.0 | 综合分修正上下限 |
| `INTRADAY_ADJ_PER_VOL` | 12.0 | 每单位日波动率 → 修正分 |
| `INTRADAY_VOL_FLOOR` | 0.1 (%) | 日波动率下限，过小回退 |
| `INTRADAY_VOL_FALLBACK` | 1.5 (%) | 无波动率估计时回退值 |

公式：`z = change_pct / vol`，`adj = clamp(-18, +18, z * 12)`（engine.py:125-126）。
仅「当日实时」且存在 SNAPSHOT 生效（engine.py:547-554），历史回填不改分。

### M1.5 盘中强度融入综合分 (engine.py:556-560)
- `live` 权重 `w = 0.35`，`lunch` 权重 `w = 0.20` (engine.py:558)。
- `composite_final = clamp( (1-w)*c_base + w*intraday_strength_score )`。`post_close` 不混入。

### M1.6 档位决策 `decide_tier`
**优先级（v3.0）：**
1. 用最终 `composite` 直接映射基础档位。市场趋势已经通过 `market_score` 进入综合分，`WEAK/BEAR` 不再在档位层二次扣分。
2. `risk.veto` → `NO_PARTICIPATE`（仅「大盘 BEAR 且宽基/宽度数据缺失」）。
3. `risk.chase_high` → `NO_CHASE_HIGH`。
4. ATR 高波动和看空量价属于软风险；即使同时命中，合计最多把基础档位下调一档，不修改展示的综合分。
5. 阈值（来自 `thresholds` 配置）：`opportunity_enhance=85` / `small_position=75` / `join_observe=60`。
6. 强约束门控：
   - `fund_flow_strong`：`score ≥ 70` **且** `consecutive_positive_days ≥ 3`。
   - `etf_rs_strong`：`score ≥ 60`。
   - `OPPORTUNITY_ENHANCE` 需 `c ≥ 85 且 fund_flow_strong 且 etf_rs_strong`。
7. 档位映射：`≥85 & 双强→OPPORTUNITY_ENHANCE` / `≥75→SMALL_POSITION` / `≥60→OBSERVE` / `else→NO_PARTICIPATE`。
8. 强势突破（breakout_volume 或 segment_up 且 etf_rs_strong）可上调一档；与任何软降档互斥。
9. `supporting_metrics` 保存 `base_signal_type / decision_score / decision_adjustments`，解释展示分数、基础档位和最终档位之间的关系。

### M1.7 仓位区间 & 失效条件
- `POSITION_RANGE` (engine.py:40-47)：`NO_PARTICIPATE/OBSERVE=[0,0]/[0,10]`、`SMALL_POSITION=[10,25]`、`OPPORTUNITY_ENHANCE=[25,50]`、`NO_CHASE_HIGH/MARKET_RISK_HIGH=[0,0]`。
- `invalidation_conditions`：`close_below_ma20` / `rsi_overheat`（读取配置阈值）/ `data_incomplete`。市场方向已计入综合分，不再单独让每只 ETF 的信号失效。

---

## M2. 盘中强度与 R1/R2 — `backend/app/opinion_engine/intraday_strength.py`

### M2.1 五因子强度 `intraday_strength` (intraday_strength.py:32-101)
权重（`weights`，intraday_strength.py:91）：`rel_market=30, volume=20, ma=20, fund=20, chip=10`。
`chip`（筹码）当前无采集 → 恒缺失 → 其余权重重归一化。

| 因子 | 计算 | 行 |
|---|---|---|
| 相对大盘 `rel_market` | `clamp(50 + (etf_chg - idx_chg) * 25)` | 59 |
| 量能 `volume` | `clamp(50 + (vr - 1) * 80)`，vr=当日累计量/进度预期量 | 68 |
| 均线多空 `ma` | 站上 VWAP 且站上 1m MA20 → **78**；任一 → **58**；皆否 → **28** | 79/81/83 |
| 资金 `fund` | `clamp(sector_flow_score)` | 87 |
| 筹码 `chip` | 缺失 | — |

`lean`：`score ≥ 60 → 看多`；`≤ 40 → 看空`；否则中性（intraday_strength.py:94）。

### M2.2 R1 / R2 持仓告警 `check_r1_r2` (intraday_strength.py:127-190)
- **R1 补仓看多**：收盘站上 MA20 **且** 连续站上 ≥ 2 天，且（资金可用且为正 或 资金不可用）（intraday_strength.py:167-169）。
- **R2 超跌抄底**：`near_lower`（收盘 ≤ 布林下轨 × 1.02）**且** `vol_ratio > 1.5` **且** `RSI(6) < 20`（intraday_strength.py:176-184）。

---

## M3. 收盘后三档价位 — `backend/app/opinion_engine/levels.py`

`compute_trade_plan(daily_df, etf_ind, lookback=20)`（levels.py:26-129）。样本不足（<5 日）直接返回空计划。

| 价位 | 计算（levels.py 行） | 说明 |
|---|---|---|
| 突破价 `breakout_price` | `max(recent_high, boll_upper, last_close*1.005)` | 前高与布林上轨共同约束 |
| 加仓价 `add_price` | 当前价下方 `ma20/boll_mid/recent_low` 中最近的有效支撑 | 回踩支撑才加仓 |
| 止损价 `stop_price` | 加仓价下方 `recent_low/boll_lower/1.5ATR` 中最近的有效保护位 | 单调关系保护 `stop < add < breakout` |
| 明日预期 | `±1 ATR`（价格换算）；无 ATR 回退近 5 日波动 sd，再无则兜底 **2.0%** (108-119) | |
| 明日倾向 | `regime_tomorrow` ∈ {偏多, 偏弱, 震荡}（按 last_close vs MA20 / MA20 斜率）(122-127) | |

布林带由指标层统一产出；止损候选在最终加仓价确定后计算，保证三档价单调。

---

## M4. 指标引擎 — `backend/app/indicator_engine/`

### M4.1 `IndicatorEngine.compute` (indicator_engine/engine.py:18-54)
产出字段：`ma20, ma20_slope, rsi14, macd, mom_5, mom_10, mom_20, vol_ratio, atr_pct, rs_20d, boll_upper, boll_mid, boll_lower`。

### M4.2 纯指标 (indicator_engine/indicators.py)
| 指标 | 公式 / 参数 | 行 |
|---|---|---|
| `rsi(n=14)` | Wilder RSI（ewm α=1/n）；全涨→100，全平→50 | 44-61 |
| `macd(12,26,9)` | 返回 `{dif,dea,hist}`；`hist=(dif-dea)*2` | 64-79 |
| `momentum(n)` | `close/close.shift(n) - 1` | 82-91 |
| `vol_ratio(n=5)` | 最新量 / 前 5 日均量（取 `iloc[-n-1:-1]`） | 110-119 |
| `atr(n=14)` | Wilder ATR | 122-135 |
| `atr_pct` | `atr / close * 100` | 138-147 |
| `ma_slope_pct(n=20, lookback=5)` | `(MA_now/MA_prev - 1) * 100`（已是百分比） | 31-41 |
| `rolling_rs(n=20)` | 共同交易日对齐后，`(target_t/target_t-20)/(base_t/base_t-20)` | — |
> ⚠️ A6：`macd`、`mom_10` 被计算但**从未被任何策略/意见/持仓逻辑读取**（死计算）。

---

## M5. 板块趋势与资金流 — `backend/app/sector_engine/engine.py`

### M5.1 板块趋势 `evaluate_sector_trend` (sector_engine/engine.py:27-83)
`score` 起点 0，上限 `clamp`（实际最高 **90**）：

| 条件 | +分 | 行 |
|---|---|---|
| 收盘 > MA20 | 35 | 50 |
| MA20 斜率 > 0 | 20 | 52 |
| `mom_20 > 0` | 15 | 58 |
| `50 ≤ RSI14 ≤ 70` | 15（健康）| 63-65 |
| `RSI14 > 80` | 0，置 `risk_overheat=True` | 66-68 |
| `40 ≤ RSI14 < 50` | 8 | 69-70 |
| `mom_5 > 0` | 5 | 73 |

降级路径 `_evaluate_sector_trend_from_change`（仅 change_percent，无收盘价的源）：`avg5>0→+40`、`up_ratio≥0.6→+25`、加速→+10、近 3 日均涨>5%→过热（sector_engine/engine.py:85-118）。

### M5.2 资金持续性 `evaluate_fund_flow`
**仅同源且至少 3 个真实观测**。`main_net_inflow` 为空的价格行不能充当资金流样本；未指定来源时，从真实非空样本中选择观测最多、日期最新的一源。不足 3 个观测返回缺失，由综合评分重归一化并下调置信度，不能按 0 分拖低综合分。最高 **80**：

| 条件 | +分 | 行 |
|---|---|---|
| 末端连续为正天数 ≥ 3 | 40 | 151 |
| == 2 | 25 | 153 |
| == 1 | 10 | 155 |
| 净流入强度 `inflow/amount > 0.01` | 30 | 165 |
| `> 0` | 15 | 167 |
| `> -0.01` | 5 | 169 |
| 大单同向确认 | +10 | 180 |
| 大单背离 | −10（下限 0）| 182-183 |

---

## M6. 风险引擎 — `backend/app/risk_engine/engine.py`

常量（risk_engine/engine.py:21-23）：`ATR_PCT_HIGH_VOL = 4.0`、`DRAWDOWN_RISK_PCT = 15.0`。

| 触发 | 动作 | 行 |
|---|---|---|
| `rsi14 > thresholds.rsi_overheat` | `chase_high + downgrade` | — |
| `sector_surge`（板块/ETF 短期涨幅过大，mom_5>0.15）| `chase_high + downgrade` | 48-51 |
| `market_regime ∈ {WEAK, BEAR}` | 仅记录市场趋势原因，不置 `high_vol`/`downgrade` | — |
| `atr_pct > 4.0` | `high_vol + downgrade` | 59-62 |
| `drawdown_pct < -15.0` | **仅 append reason `drawdown_from_high`，不置任何 flag** | 65-66 |
| 否决 `veto` | `regime==BEAR 且 missing_data`（受 `deny_market_bear_with_missing_data` 开关）| 69-75 |
| `downgrade_on_chase_high` | 仅控制追高相关降级；不能关闭真实 ATR 高波动风险 | — |

> ⚠️ A8：回撤 `> -15%` 仅记 reason，对档位/否决无任何影响（纯装饰）。

---

## M7. 配置与策略参数 — `backend/app/config.py`（`StrategyConfig` 等）

| 参数 | 默认值 | 行 |
|---|---|---|
| `composite_weights` | `market=0.25, sector_trend=0.25, fund_flow=0.25, etf_rs=0.25` | 136-143 |
| `thresholds.join_observe` | 60 | 146 |
| `thresholds.small_position` | 75 | 147 |
| `thresholds.opportunity_enhance` | 85 | 148 |
| `thresholds.rsi_overheat` | 80（风险判断与失效条件共同读取）| 149 |
| `risk_filter.deny_market_bear_with_missing_data` | True | 154 |
| `risk_filter.downgrade_on_chase_high` | True | 155 |
| `broad_index_codes` | `000300, 000001, 399001` | 128-130 |
| `backfill.lookback_days` | 250 | 168 |
| `scheduler.intraday_signal_interval_seconds` | 300（盘中每 5min）| 106 |
| `data_quality.max_abs_change_percent` | 11.0 | 94 |
| `data_quality.max_price_span_ratio` | 4.0 | 98 |
| `data_quality.delay_seconds_threshold` / `stale_seconds_threshold` | 120 / 1800 | 92-93 |

---

## M8. 规则冻结与版本化 — `backend/app/strategy_engine/rules.py` + `backend/app/strategy_versioning.py`

- `RULES_V1`：纯文本规则字典，版本字符串 **"3.0"**，含 market_score / sector_trend_score / fund_flow_score / etf_rs_score / risk_filter / signal_synthesis / tiers / volume_price_ta / intraday_momentum。规则变化参与策略哈希，旧信号版本保持不变。
- `compute_strategy_hash(params, rules)`（strategy_versioning.py:16-24）：`SHA256(canonical JSON of {params, rules})`。
- `build_version_string`（strategy_versioning.py:27-29）：`f"{base}-{hash[:6]}"`。
- 参与哈希的 `params`（strategy_versioning.py:37-41、57-61）：**仅** `composite_weights / thresholds / risk_filter` 三个 config 字典；`rules` 在 `current_strategy_version` 传 `{}`，在 `mint_strategy_version` 传 `RULES_V1`。
  > 治理要求：纯代码常量本身不自动进入哈希；关键行为变更必须同步更新 `RULES_V1`，从而生成新 `strategy_version`。

---

## M9. 文案渲染 — `backend/app/opinion_engine/templates.py`

- `TIER_TEXT` / `REGIME_TEXT` / `POSITION_TEXT`（templates.py:16-43）：英文码 → 中文展示（含 `MARKET_RISK_HIGH` 文案，尽管引擎已不产出，见 A4）。
- 模板：`TEMPLATE_V1` / `TEMPLATE_LIVE` / `TEMPLATE_LUNCH`（templates.py:45-61）。
- `key_metrics_text`（templates.py:122-184）：阈值化人话（RSI>70 超买 / <30 超卖；rs>1.05 强于大盘等）。
- `basis_text`（templates.py:187-304）：专业「分析依据」叙述。
  `MA20 斜率` 直接按 `ma_slope_pct` 的百分比值展示，不再二次缩放。

---

## M10. 持仓分析动作推导 — `backend/app/portfolio/analyzer.py`

常量（analyzer.py:21-23）：`MAX_POSITIONS = 20`、`STALE_THRESHOLD_DAYS = 5`。

`_decide_action` 优先级（analyzer.py:54-91）：`EXIT > REDUCE > RECONFIRM > HOLD`。

| 动作 | 触发条件 |
|---|---|
| `EXIT` | 零仓位档位、`veto`、`etf_rs_20d` 低于配置阈值或 `close_below_ma20` |
| `REDUCE` | 实际仓位高于建议上限、`downgrade`、综合分下降达到配置阈值或 `NO_CHASE_HIGH` |
| `RECONFIRM` | `OBSERVE`、有 failed_rules 或信号过期 |
| `HOLD` | 其余 | 91 |

`WEAK/BEAR` 不再直接让所有持仓 RECONFIRM/EXIT；市场方向已进入公共信号分数。`rs_negative` 阈值默认 `0.95`，可配置。

---

## M11. 调度相位与触发 — `backend/app/worker.py`

| 任务 | Cron | 相位 | 行 |
|---|---|---|---|
| `intraday_signal` | 每 300s | live | — |
| `lunch_opinion` | 午盘 ~11:40 | lunch | — |
| `pre_close_evaluate` | **14:50** | pre_close | 348 |
| `post_close_evaluate` | 15:10 | post_close | — |

---

## 当前审计状态

| 编号 | 严重度 | 模块 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| **A1（已修复）** | — | M3/M4 | 指标层已产出三条布林带并接入 R2/三档价 | 布林分支恢复 | 已完成 |
| **A2（已修复）** | — | M1/M4 | RS 已按共同交易日的 20 日增长倍数计算 | 相对强弱恢复 | 已完成 |
| **A3（已修复）** | — | M1/M7 | RSI 过热阈值读取 `thresholds.rsi_overheat` | 配置与执行一致 | 已完成 |
| **A4（已修复）** | — | M8/M1 | v3.0 已从活动规则中移除 `MARKET_RISK_HIGH`；枚举仅为读取历史存量数据保留 | 规则文本与当前实现一致 | 已完成 |
| **A5（已修复）** | — | M9 | MA20 斜率按指标原始百分比展示，不再乘 100 | 展示与计算一致 | 已完成 |
| **A6（非阻塞）** | 低 | M4 | `macd`、`mom_10` 为预计算指标，尚未进入评分 | 少量计算开销 | 接入前先定义可回测规则，不能临时加权 |
| **A7（已修复）** | — | M11 | docstring 与 Cron 均为 14:50 | 文档一致 | 已完成 |
| **A8（设计选择）** | — | M6 | 深度回撤仅写风险原因，不自动降档 | 避免把趋势破坏和超跌混为一谈 | 保持提示 |
| **A9（部分缓解）** | 中(治理) | M8 | 规则字典与配置参与哈希；纯代码常量仍需同步进规则字典 | 漏同步会影响审计 | 关键行为改动必须 bump `RULES_V1` |
| **A10（已修复）** | — | M3 | 止损在最终加仓价确定后计算 | 保证 `stop < add < breakout` | 已完成 |

---

## 审核要点清单（给 reviewer）

1. 核对同一市场方向是否只通过 `market_score` 进入一次，档位层和持仓层不得再次按 WEAK/BEAR 扣分或一刀切。
2. 核对资金流是否至少有 3 个真实、同源观测；空值不能视作 0 分。
3. 核对展示分、基础档位、调档原因和最终档位是否能由 `supporting_metrics` 完整复算。
4. 关键行为变化必须更新 `RULES_V1`，确保策略哈希变化；纯展示文案变更可不改变交易规则版本。
5. 新增指标必须先给出可回测规则与样本外验证，不能为了增加信号数量临时降低阈值或堆叠因子。
