"""评估流水线幂等单测（P3，§7.1）。

同 as_of 重跑 post_collection_evaluate：
- signal / opinion 行数不变（写入一次 + 原地更新）。
- signal_id / opinion_id 稳定。
- strategy_version 不重复（baseline + P3 共 2 行）。
"""
from datetime import date

from sqlalchemy import select

from app.config import get_settings
from app.db import init_db, make_engine, session_scope
from app.db.models.mapping import StrategyVersion
from app.db.models.signal_opinion import Opinion, Signal
from app.evaluation.pipeline import post_collection_evaluate
from app.repository import mapping_repo


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    return s, eng


def _seed_mappings(session):
    for code, idx in [("510300", "000300"), ("512010", "000300")]:
        mapping_repo.upsert_mapping(
            session, etf_code=code, etf_name=code,
            related_sector_codes=["BK0465"], related_index_code=idx,
            category="t", mapping_version="v1",
            valid_from=date(2000, 1, 1), valid_to=None, notes="t",
        )


def test_post_collection_evaluate_idempotent(tmp_path):
    s, eng = _setup(tmp_path)
    as_of = date(2024, 1, 3)
    with session_scope(eng) as session:
        _seed_mappings(session)
        r1 = post_collection_evaluate(session, s, phase="post_close", as_of=as_of)
        sig1 = session.execute(select(Signal)).scalars().all()
        opin1 = session.execute(select(Opinion)).scalars().all()
        ids1 = {x.signal_id for x in sig1}
        opin_ids1 = {x.opinion_id for x in opin1}

    assert r1["signals_written"] == 2 and r1["opinions_written"] == 2
    assert len(sig1) == 2 and len(opin1) == 2

    with session_scope(eng) as session:
        r2 = post_collection_evaluate(session, s, phase="post_close", as_of=as_of)
        sig2 = session.execute(select(Signal)).scalars().all()
        opin2 = session.execute(select(Opinion)).scalars().all()
        ids2 = {x.signal_id for x in sig2}
        opin_ids2 = {x.opinion_id for x in opin2}

    assert r2["signals_updated"] == 2 and r2["opinions_updated"] == 2
    assert len(sig2) == 2 and len(opin2) == 2  # 行数不变
    assert ids1 == ids2  # signal_id 稳定
    assert opin_ids1 == opin_ids2  # opinion_id 稳定

    with session_scope(eng) as session:
        versions = session.execute(select(StrategyVersion)).scalars().all()
        # baseline（init_db）+ P3 rules-v1，共 2 行，不重复
        assert len(versions) == 2


def test_pre_close_and_post_close_distinct_opinions(tmp_path):
    s, eng = _setup(tmp_path)
    as_of = date(2024, 1, 3)
    with session_scope(eng) as session:
        _seed_mappings(session)
        post_collection_evaluate(session, s, phase="pre_close", as_of=as_of)
        post_collection_evaluate(session, s, phase="post_close", as_of=as_of)
        opin = session.execute(select(Opinion)).scalars().all()
        phases = {o.phase for o in opin}
        # 每个 ETF 各有 pre_close + post_close 两条意见（按 signal_id+phase 区分）
        assert phases == {"pre_close", "post_close"}
        assert len(opin) == 4
