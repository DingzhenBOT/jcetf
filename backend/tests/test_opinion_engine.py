"""opinion_engine 单测（P3）：template-v2 确定性生成（人话化），无 LLM 调用。"""
from app.opinion_engine.engine import OpinionEngine
from app.opinion_engine.phrase import TemplatePhraseClient
from app.opinion_engine.templates import TIER_TEXT


def _signal(tier="SMALL_POSITION", score=78.0, confidence=85, regime="TREND_UP", position_range=None):
    return {
        "target_etf": "510300",
        "signal_type": tier,
        "score": score,
        "confidence": confidence,
        "market_regime": regime,
        "suggested_position_range": position_range if position_range is not None else [10, 25],
        "supporting_metrics": {
            "etf_rsi14": 55,
            "etf_rs_20d": 1.05,
            "sector_score": 70,
            "fund_flow_score": 80,
            "advance_ratio": 0.62,
        },
        "review_time": None,
    }


def test_generate_contains_etf_tier_score():
    opin = OpinionEngine().generate(_signal(), "post_close", {})
    assert opin["template_version"] == "template-v2"
    assert opin["model_version"] is None
    assert "510300" in opin["content"]
    assert TIER_TEXT["SMALL_POSITION"] in opin["content"]
    assert "78" in opin["content"]
    assert "85" in opin["content"]


def test_template_phrase_client_deterministic():
    p = TemplatePhraseClient()
    assert p.phrase("abc") == "abc"


def test_no_llm_called_by_default():
    # 默认 OpinionEngine 用 TemplatePhraseClient，不触发任何网络 / LLM
    eng = OpinionEngine()
    assert isinstance(eng.phrase, TemplatePhraseClient)
    out = eng.generate(_signal(tier="NO_CHASE_HIGH", score=90, confidence=90), "pre_close", {})
    assert "别追高" in out["content"]
    assert "降低风险敞口" not in out["content"]


def test_position_text_present():
    out = OpinionEngine().generate(
        _signal(tier="OBSERVE", score=65, confidence=70, position_range=[0, 10]),
        "post_close",
        {},
    )
    assert "轻仓试错" in out["content"]
    assert "（0-10%）" in out["content"]


def test_vp_in_one_liner():
    # 方案B：量价状态与形态应出现在 one_liner（key_metrics_text）中
    sig = _signal(tier="SMALL_POSITION", score=78, confidence=85)
    sig["supporting_metrics"] = {
        **sig["supporting_metrics"],
        "vp_state_text": "放量上涨",
        "vp_vol_ratio_state": "放量",
        "vp_patterns": ["breakout_volume", "segment_up"],
    }
    out = OpinionEngine().generate(sig, "midday", {})
    assert "放量上涨" in out["content"]
    assert "放量突破" in out["content"]
    assert "分段量涨阳线" in out["content"]


def test_missing_data_note():
    out = OpinionEngine().generate(
        _signal(tier="NO_PARTICIPATE", score=None, confidence=40),
        "post_close",
        {},
    )
    assert "数据不足" in out["content"] or "—" in out["content"]


def test_generate_returns_basis_text():
    # 引擎应一并返回专业「分析依据」叙述（前端「查看依据」渲染，替代原始 KV）
    out = OpinionEngine().generate(_signal(), "post_close", {"etf_code": "510300"})
    assert "basis_text" in out
    assert isinstance(out["basis_text"], str) and len(out["basis_text"]) > 0
    assert "510300" in out["basis_text"]


def test_basis_text_explains_component_weights_and_adjustment():
    from app.opinion_engine.templates import basis_text

    text = basis_text(
        {
            "market_regime": "WEAK",
            "component_scores": {"market": 35.0, "etf_rs": 72.0},
            "effective_weights": {"market": 0.5, "etf_rs": 0.5},
            "base_signal_type": "OBSERVE",
            "decision_adjustments": ["risk_downgrade_one_tier"],
        },
        {"etf_code": "510300"},
        "post_close",
    )

    assert "基础档位下调一档" in text
    assert "市场35.0分，有效权重50%" in text
    assert "ETF相对强弱72.0分，有效权重50%" in text


def test_basis_text_offexchange_honest():
    # 场外联接基金：无场内K线/板块/资金，应诚实说明数据缺失，而非谎称中性
    from app.opinion_engine.templates import basis_text

    sup = {
        "etf_rsi14": None, "etf_rs_20d": None, "etf_ma20_slope": None, "etf_atr_pct": None,
        "sector_score": None, "fund_flow_score": None, "advance_ratio": 0.48, "market_regime": "WEAK",
        "vp_state": None, "vp_state_text": None, "vp_patterns": [],
    }
    inp = {"etf_code": "110020", "sector_code": None, "related_index_code": "000300", "market_regime": "WEAK"}
    text = basis_text(sup, inp, "post_close")
    assert "未获取到该标的场内日 K 线" in text
    assert "ETF 技术面、板块趋势、资金持续性 缺失" in text
    assert "110020" in text


def test_basis_text_full_data():
    # 场内 ETF 数据齐全：应给出 RSI/相对强弱/均线/波动率/板块/资金的完整叙述
    from app.opinion_engine.templates import basis_text

    sup = {
        "etf_rsi14": 62.0, "etf_rs_20d": 1.08, "etf_ma20_slope": 0.4, "etf_atr_pct": 1.9,
        "sector_score": 68, "fund_flow_score": 72, "advance_ratio": 0.63, "market_regime": "TREND_UP",
        "vp_state_text": "价升量增", "vp_patterns": ["breakout_volume"],
    }
    inp = {"etf_code": "510300", "sector_code": "BK0428", "related_index_code": "000300", "market_regime": "TREND_UP"}
    text = basis_text(sup, inp, "post_close")
    assert "RSI14=62" in text
    assert "RS=1.08" in text
    assert "MA20 斜率 +0.4%" in text
    assert "板块趋势评分 68" in text
    assert "资金持续性 72" in text
    # 数据齐全时不应出现缺失说明
    assert "缺失" not in text
