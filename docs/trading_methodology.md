# CJETF 交易方法论审计文档（硬编码逻辑全表）

> 用途：把后端**所有写死（hard-coded）的交易方法论 / 交易逻辑**按「模块 + 方法论」整理，供人工审核。
> 范围：仅含确定性规则（无 LLM / 无外部推断）。每条标注 `file:line` 与精确常量，方便定位与回归。
> 最后核对：基于 `main` 分支 `057c1dd`（C24 场外基金方案B）后代码逐文件复核。
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
> ⚠️ 因 `rolling_rs` 实现缺陷（A2），`rs_20d ≈ 0` → `etf_rs_score` 恒为 **0**。

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

### M1.6 档位决策 `decide_tier` (engine.py:182-266)
**优先级（C23：市场弱改为降档修正，不再一票否决）：**
1. `risk.veto` → `NO_PARTICIPATE`（仅「大盘 BEAR 且数据缺失」）。
2. `risk.chase_high` → `NO_CHASE_HIGH`。
3. **降档修正**（可叠加，engine.py:213-221）：

| 条件 | 综合分扣减 |
|---|---|
| `market_regime == BEAR` | −18 |
| `market_regime == WEAK` | −10 |
| `risk.high_vol` | −5 |
| `risk.downgrade` | −15 |

   最坏叠加 = −(18+10+5+15) = **−38**（实际 BEAR+downgrade+high_vol 同时出现时）。
4. 阈值（engine.py:223-225，来自 `thresholds` 配置）：`opportunity_enhance=85` / `small_position=75` / `join_observe=60`。
5. 强约束门控（engine.py:227-237）：
   - `fund_flow_strong`：`score ≥ 70` **且** `consecutive_positive_days ≥ 3`。
   - `etf_rs_strong`：`score ≥ 60`（因 A2 实际恒不满足 → 见影响）。
   - `OPPORTUNITY_ENHANCE` 需 `c ≥ 85 且 fund_flow_strong 且 etf_rs_strong`。
6. 档位映射（engine.py:239-266）：`≥85 & 双强→OPPORTUNITY_ENHANCE` / `≥75→SMALL_POSITION` / `≥60→OBSERVE` / `else→NO_PARTICIPATE`。
7. 方案B 量价增强（engine.py:250-266）：看空形态优先降一档（`_vp_bearish`，divergence / (anomaly 且下跌方向) / VOL_UP_FALL）；否则强势突破（breakout_volume 或 segment_up 且 etf_rs_strong）上调一档。

### M1.7 仓位区间 & 失效条件
- `POSITION_RANGE` (engine.py:40-47)：`NO_PARTICIPATE/OBSERVE=[0,0]/[0,10]`、`SMALL_POSITION=[10,25]`、`OPPORTUNITY_ENHANCE=[25,50]`、`NO_CHASE_HIGH/MARKET_RISK_HIGH=[0,0]`。
- `invalidation_conditions`（engine.py:676-685）：`close_below_ma20` / `market_regime_bear` / `rsi_overheat_gt_80`（**硬编码** `rsi14 > 80`，非读配置，见 A3）/ `data_incomplete`。

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
  > ⚠️ `boll_lower` 由 `etf_ind.get("boll_lower")` 读取，而 `IndicatorEngine` **从不产出**该值（A1）→ `near_lower` 恒 False → **R2 永不触发**。

---

## M3. 收盘后三档价位 — `backend/app/opinion_engine/levels.py`

`compute_trade_plan(daily_df, etf_ind, lookback=20)`（levels.py:26-129）。样本不足（<5 日）直接返回空计划。

| 价位 | 计算（levels.py 行） | 说明 |
|---|---|---|
| 突破价 `breakout_price` | `max(recent_high, boll_upper, last_close*1.005)` (78-81) | 布林上轨恒 None（A1）→ 仅 `max(前高, 现价*1.005)` |
| 加仓价 `add_price` | `min(ma20, boll_mid, recent_low 中 < 现价者)` (86-89) | 布林中轨恒 None → 仅 MA20 / 前低 |
| 止损价 `stop_price` | `min(recent_low, boll_lower, last_close*(1-1.5·atr%))` (92-104) | 布林下轨恒 None；单调关系保护 `stop < add < breakout` |
| 明日预期 | `±1 ATR`（价格换算）；无 ATR 回退近 5 日波动 sd，再无则兜底 **2.0%** (108-119) | |
| 明日倾向 | `regime_tomorrow` ∈ {偏多, 偏弱, 震荡}（按 last_close vs MA20 / MA20 斜率）(122-127) | |

> ⚠️ A1 影响：三档价全部退化为「前高 / 前低 / MA20 / ATR」，**布林带三值完全不参与**（尽管代码已正确读取，只差指标层产出）。
> ⚠️ A10：加仓价回退分支（levels.py:101-103）改写了 `add_price` 后，`stop` 仍基于旧 `add` 计算，未重算，可能破坏 `stop < add` 单调关系。

---

## M4. 指标引擎 — `backend/app/indicator_engine/`

### M4.1 `IndicatorEngine.compute` (indicator_engine/engine.py:18-54)
产出字段：`ma20, ma20_slope, rsi14, macd, mom_5, mom_10, mom_20, vol_ratio, atr_pct, rs_20d`。
> ⚠️ **不产出 `boll_upper / boll_mid / boll_lower`** —— 这是 A1 的根因。

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
| `rolling_rs(n=20)` | **见 A2：代码算 `Π r_t / Π r_b`，docstring 写 `Π(1+r_t)/Π(1+r_b)`** | 150-163 |

> ⚠️ A2：`rolling_rs` 当前 `t_ret = (t/t.shift(1) - 1)` 再 `.prod()` = 日收益率连乘（对小幅收益≈0）；正确应为 `t_ret = t/t.shift(1)` 再 `.prod()`（= 累计收益比 = `close_n/close_0`）。
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

### M5.2 资金持续性 `evaluate_fund_flow` (sector_engine/engine.py:120-191)
**仅同源**（`metric_source` 过滤，sector_engine/engine.py:134-138）。最高 **80**：

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
| `rsi14 > 80` | `chase_high + downgrade`（reason `rsi_overheat>80`）| 43-46 |
| `sector_surge`（板块/ETF 短期涨幅过大，mom_5>0.15）| `chase_high + downgrade` | 48-51 |
| `market_regime ∈ {WEAK, BEAR}` | `high_vol` | 54-56 |
| `atr_pct > 4.0` | `high_vol + downgrade` | 59-62 |
| `drawdown_pct < -15.0` | **仅 append reason `drawdown_from_high`，不置任何 flag** | 65-66 |
| 否决 `veto` | `regime==BEAR 且 missing_data`（受 `deny_market_bear_with_missing_data` 开关）| 69-75 |
| `downgrade` 总开关 | 受 `downgrade_on_chase_high` 约束（关闭则不降级）| 78-79 |

> ⚠️ A8：回撤 `> -15%` 仅记 reason，对档位/否决无任何影响（纯装饰）。

---

## M7. 配置与策略参数 — `backend/app/config.py`（`StrategyConfig` 等）

| 参数 | 默认值 | 行 |
|---|---|---|
| `composite_weights` | `market=0.25, sector_trend=0.25, fund_flow=0.25, etf_rs=0.25` | 136-143 |
| `thresholds.join_observe` | 60 | 146 |
| `thresholds.small_position` | 75 | 147 |
| `thresholds.opportunity_enhance` | 85 | 148 |
| `thresholds.rsi_overheat` | 80（**未被读取**，见 A3）| 149 |
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

- `RULES_V1`（rules.py:17）：纯文本规则字典，版本字符串 **"2.2"**，含 market_score / sector_trend_score / fund_flow_score / etf_rs_score / risk_filter / signal_synthesis / tiers / volume_price_ta / intraday_momentum。其 `weights` 段与 M5/M1 引擎常量**一致**（便于审计）。
- `compute_strategy_hash(params, rules)`（strategy_versioning.py:16-24）：`SHA256(canonical JSON of {params, rules})`。
- `build_version_string`（strategy_versioning.py:27-29）：`f"{base}-{hash[:6]}"`。
- 参与哈希的 `params`（strategy_versioning.py:37-41、57-61）：**仅** `composite_weights / thresholds / risk_filter` 三个 config 字典；`rules` 在 `current_strategy_version` 传 `{}`，在 `mint_strategy_version` 传 `RULES_V1`。
  > ⚠️ A9：数十个**引擎内硬编码常量**（M1±35/−18、M2 因子分、M3 价位、M5/M6 评分）**不参与哈希**。改动它们不会生成新 `strategy_version`，旧 Signal 仍标旧版本号但行为已变，审计/回放失真。

---

## M9. 文案渲染 — `backend/app/opinion_engine/templates.py`

- `TIER_TEXT` / `REGIME_TEXT` / `POSITION_TEXT`（templates.py:16-43）：英文码 → 中文展示（含 `MARKET_RISK_HIGH` 文案，尽管引擎已不产出，见 A4）。
- 模板：`TEMPLATE_V1` / `TEMPLATE_LIVE` / `TEMPLATE_LUNCH`（templates.py:45-61）。
- `key_metrics_text`（templates.py:122-184）：阈值化人话（RSI>70 超买 / <30 超卖；rs>1.05 强于大盘等）。
- `basis_text`（templates.py:187-304）：专业「分析依据」叙述。
  > ⚠️ A5：`MA20 斜率 {slope * 100:+.1f}%`（templates.py:256）。`slope` 来自 `ma_slope_pct` **已是百分比**（如 2.0 表示 2%），再 ×100 → 显示 **200.0%**，放大 100 倍。

---

## M10. 持仓分析动作推导 — `backend/app/portfolio/analyzer.py`

常量（analyzer.py:21-23）：`MAX_POSITIONS = 20`、`STALE_THRESHOLD_DAYS = 5`。

`_decide_action` 优先级（analyzer.py:54-91）：`EXIT > REDUCE > RECONFIRM > HOLD`。

| 动作 | 触发条件 |
|---|---|
| `EXIT` | `veto` 或 `regime==BEAR` 或 `etf_rs_20d < 1.0`（跑输基准）或 `close_below_ma20` | 69 |
| `REDUCE` | `downgrade` 或 综合分下降 `≥ 5` 或 `signal_type==NO_CHASE_HIGH` | 73-79 |
| `RECONFIRM` | `NO_PARTICIPATE/OBSERVE` 或 有 failed_rules 或 `regime==WEAK` 或 信号过期 `> 5 天` | 83-88 |
| `HOLD` | 其余 | 91 |

`rs_negative`：`etf_rs_20d < 1.0`（analyzer.py:46-51）。

---

## M11. 调度相位与触发 — `backend/app/worker.py`

| 任务 | Cron | 相位 | 行 |
|---|---|---|---|
| `intraday_signal` | 每 300s | live | — |
| `lunch_opinion` | 午盘 ~11:40 | lunch | — |
| `pre_close_evaluate` | **14:50** | pre_close | 348 |
| `post_close_evaluate` | 15:10 | post_close | — |

> ⚠️ A7：`job_pre_close_evaluate` docstring 写 "14:59"（worker.py:229），实际 Cron `minute=50`（worker.py:348）。

---

## 审计发现汇总（A1–A10）

| 编号 | 严重度 | 模块 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| **A1** | 高 | M3/M4 | `IndicatorEngine` 不产出 `boll_upper/mid/lower`，`levels.py` 与 `check_r1_r2` 恒读到 None | R2 抄底信号**永不触发**；三档价布林带三值**全部失效**（仅用前高/前低/MA20/ATR）| 在 `compute()` 增加 `boll_upper/mid/lower`（Bollinger(20,2)）；`levels.py` 已正确读取，补产出即可 |
| **A2** | 高 | M1/M4 | `rolling_rs` 算 `Π r_t / Π r_b`（日收益率连乘≈0），docstring 意图是 `Π(1+r_t)/Π(1+r_b)`（累计收益比）| `rs_20d ≈ 0` → `etf_rs_score` 恒 0 → `etf_rs` 组件恒失效、`etf_rs_strong` 永不真 → `OPPORTUNITY_ENHANCE` 档位几乎无法触发 | 改为 `t_ret = t / t.shift(1)`（去掉 `-1`）后 `.prod()`；补单测断言 `rs_20d ≈ close_n/close_0` |
| **A3** | 中 | M1/M7 | 配置 `thresholds.rsi_overheat=80` 从未被读取；`rsi>80` 硬编码于 engine.py:683、risk_engine:43 | 配置项形同虚设，改 YAML 不生效，易误导 | 用 `thresholds.get("rsi_overheat", 80)` 读取，或删除该配置项 |
| **A4** | 中 | M8/M1 | `rules.py` 的 `tiers` 仍把 `MARKET_RISK_HIGH` 列为「market_regime∈{WEAK,BEAR} 或 high_vol」产出档位，但 C23 起引擎已改为降档、不再产出该档位 | 规则文本与实现偏离，审计误读 | 更新 `rules.py` 注释/段，标注「C23 起不再产出，降级透传 caution」|
| **A5** | 中 | M9 | `basis_text` 对已是百分比的 `slope` 再 `×100`，显示放大 100 倍（2%→"200.0%"）| 前端「分析依据」中 MA20 斜率数值严重失真 | 去掉 `×100`，直接用 `{slope:+.1f}%` |
| **A6** | 低 | M4 | `macd`、`mom_10` 被计算但从未被任何逻辑读取 | 浪费算力（每只 ETF 每相位），且 `macd` 有量化价值却闲置 | 接入量价/背离逻辑，或移除以减负 |
| **A7** | 低 | M11 | `pre_close_evaluate` docstring 写 "14:59"，实际 Cron 14:50 | 文档/注释不一致 | docstring 改为 14:50 |
| **A8** | 中 | M6 | `drawdown_pct < -15%` 仅 append reason，不置 veto/downgrade/high_vol | 15% 回撤风险阈值纯装饰，对档位无影响 | 明确是否要据回撤降档；若要，接入 `downgrade`；否则文档注明「仅提示」|
| **A9** | 高(治理) | M8 | 策略版本哈希仅含 3 个 config 字典；引擎内数十个硬编码常量改动不生成新版本 | 改代码逻辑不会 bump version，旧 Signal 标旧版本号但行为已变，审计/回放失真 | 把关键方法论常量（或 `rules.py` 全文 + 常量表）纳入哈希；或在改动时手动 bump `StrategyConfig.version` |
| **A10** | 低 | M3 | `levels.py` 加仓价回退分支改写 `add_price` 后未重算 `stop`，可能破坏 `stop < add` 单调关系 | 极端样本下止损价可能高于加仓价 | 回退后重算 `stop`（复用 monotonic 保护逻辑）|

---

## 审核要点清单（给 reviewer）

1. **A2 是否优先修？** 它让 ETF 相对强弱维度（占权重 25%）实际恒为 0，并锁死 `OPPORTUNITY_ENHANCE` 档位——对建议质量影响最大。
2. **A1 是否修？** 修法极简（指标层补 3 个布林值），但会同时激活 R2 抄底信号与三档价的布林分支——需确认 R2 在实盘是否过频繁。
3. **A9 版本治理**：在动手改 A1/A2/A5 之前，是否先把关键常量纳入哈希，避免「改了行为却没新版本」？
4. **A8 回撤阈值**：产品上是要「仅提示」还是「据此降档」？决定代码方向。
5. **A3/A4/A5/A6/A7/A10**：低/中危，可随主修一并清理，但属「文档与显示失真」/「死代码」，不阻塞建议正确性。

> 所有 `file:line` 基于 `main @ 057c1dd`；后续改动后请重新核对本表。
