"""opinion_engine 单测（P3）：template-v1 确定性生成，无 LLM 调用。"""
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
    assert opin["template_version"] == "template-v1"
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
