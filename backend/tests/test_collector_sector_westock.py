"""C19-G 腾讯自选股 westock-data 板块异动接引擎测试。

不依赖 npx 真实调用：mock external_data.collect_sector_movement 返回样本（含可映射/不可映射板块名），
验证：
- collect_sector_from_westock：合并 industry/concept(涨跌幅)+fund_flow(主力净流入) 为每个匹配 BK 一行，
  入库 SECTOR BAR（source='westock'，change_percent + main_net_inflow 正确），未匹配板块被跳过。
- sector_map.resolve_sector_bk：规范名/别名/子串兜底解析。
- sector_engine.evaluate_sector_trend：close 缺失时改用 change_percent 动量（westock 场景）。
"""
from datetime import date

import pandas as pd
import pytest

from app.collector import sector_map
from app.collector.collector import Collector
from app.config import get_settings
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.repository import mapping_repo


_MOVEMENT = {
    "available": True,
    "source": "腾讯自选股 westock-data",
    "industry": [
        {"name": "半导体", "changePct": 2.5, "turnoverRate": 3.1, "changePct5d": 1.0, "changePct20d": -5.0, "leadStock": "x"},
        {"name": "证券", "changePct": 1.2, "turnoverRate": 2.0, "changePct5d": 0.5, "changePct20d": -2.0, "leadStock": "y"},
        {"name": "玻璃玻纤", "changePct": 9.65, "turnoverRate": 4.2, "changePct5d": 5.9, "changePct20d": -39.0, "leadStock": "z"},  # 未映射
    ],
    "concept": [
        {"name": "新能源车", "changePct": 3.0, "turnoverRate": 2.5, "changePct5d": 2.0, "changePct20d": -10.0, "leadStock": "w"},  # 别名->BK0900
    ],
    "fund_flow": [
        {"name": "半导体", "changePct": 2.5, "mainNetInflow": 123456.0, "mainNetInflow5d": 999.0, "upDownRatio": "50/60"},
        {"name": "元件", "changePct": 6.25, "mainNetInflow": 672891.0, "mainNetInflow5d": 4975.0, "upDownRatio": "59/60"},  # 未映射
    ],
}


class _Provider(BaseDataProvider):
    def get_trade_calendar(self): return ["20260727"]
    def get_index_snapshot(self): return pd.DataFrame()
    def get_sector_ranking(self, sector_type): return pd.DataFrame()
    def get_etf_snapshot(self): return pd.DataFrame()
    def get_etf_history(self, *a, **k): return pd.DataFrame()
    def get_index_history(self, *a, **k): return pd.DataFrame()
    def get_sector_history(self, *a, **k): return pd.DataFrame()
    def get_sector_fund_flow_history(self, *a, **k): return pd.DataFrame()
    def get_market_breadth_raw(self): return pd.DataFrame()


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    s.strategy.broad_index_codes = ["000300"]
    s.backfill.major_sector_codes = ["BK1036", "BK0900", "BK0473"]
    eng = make_engine(s)
    init_db(eng, s)
    with session_scope(eng) as session:
        mapping_repo.upsert_mapping(
            session,
            etf_code="510300", etf_name="沪深300ETF",
            related_sector_codes=["BK1036", "BK0900", "BK0473"],
            related_index_code="000300", category="宽基", mapping_version="v1",
            valid_from=date(2024, 1, 1),
        )
    return s, eng


# ---- 名->BK 解析 ----
def test_resolve_sector_bk_exact_and_alias():
    codes = {"BK1036", "BK0473", "BK0900", "BK1035"}
    assert sector_map.resolve_sector_bk("半导体", codes) == "BK1036"
    assert sector_map.resolve_sector_bk("券商", codes) == "BK0473"       # 别名
    assert sector_map.resolve_sector_bk("新能源车", codes) == "BK0900"   # 别名
    assert sector_map.resolve_sector_bk("光伏设备", codes) == "BK1035"   # 子串兜底(na in n)
    assert sector_map.resolve_sector_bk("光伏", codes) == "BK1035"       # 子串兜底(n in na)


def test_resolve_sector_bk_out_of_tracked_returns_none():
    codes = {"BK1036"}
    # 规范名匹配但不在跟踪集合内 -> None
    assert sector_map.resolve_sector_bk("银行", codes) is None
    assert sector_map.resolve_sector_bk("玻璃玻纤", codes) is None  # 未收录


# ---- collector 入库 ----
def test_collect_sector_from_westock(monkeypatch, tmp_path):
    import app.services.external_data as ex
    monkeypatch.setattr(ex, "collect_sector_movement", lambda: _MOVEMENT)

    s, eng = _setup(tmp_path)
    c = Collector(_Provider(), s)
    with session_scope(eng) as session:
        res = c.collect_sector_from_westock(session, {"BK1036", "BK0900", "BK0473"}, date(2026, 7, 27))
    assert res["ok"] == 3 and res["failed"] == 0

    with session_scope(eng) as session:
        from app.db.models.market import MarketQuote
        rows = session.query(MarketQuote).filter_by(
            symbol_type="SECTOR", data_source="westock", data_kind="BAR"
        ).all()
        assert len(rows) == 3
        by_code = {r.symbol: r for r in rows}
        # 半导体：industry(涨跌幅 2.5) + fund_flow(主力净流入 123456) 合并
        assert by_code["BK1036"].change_percent == 2.5
        assert by_code["BK1036"].main_net_inflow == 123456.0
        # 证券：仅 industry 涨跌幅
        assert by_code["BK0473"].change_percent == 1.2
        assert by_code["BK0473"].main_net_inflow is None
        # 新能源车：concept 涨跌幅（别名映射）
        assert by_code["BK0900"].change_percent == 3.0


def test_collect_sector_from_westock_unavailable_degrades(monkeypatch, tmp_path):
    import app.services.external_data as ex
    monkeypatch.setattr(ex, "collect_sector_movement",
                        lambda: {"available": False, "reason": "npx 超时", "items": []})

    s, eng = _setup(tmp_path)
    c = Collector(_Provider(), s)
    with session_scope(eng) as session:
        res = c.collect_sector_from_westock(session, {"BK1036"}, date(2026, 7, 27))
    assert res["status"] == "FAILED"
    with session_scope(eng) as session:
        from app.db.models.market import MarketQuote
        rows = session.query(MarketQuote).filter_by(symbol_type="SECTOR", data_source="westock").all()
        assert len(rows) == 0  # 失败不入库


# ---- 引擎：close 缺失时改用 change_percent 动量（westock 场景） ----
def test_sector_trend_change_percent_fallback():
    from app.sector_engine.engine import SectorEngine

    eng = SectorEngine()
    # close 全缺失，仅有 change_percent 系列（westock 异动榜场景）
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-07-21", periods=5, freq="D"),
        "close": [None, None, None, None, None],
        "change_percent": [1.0, 2.0, -0.5, 3.0, 2.5],
    })
    out = eng.evaluate_sector_trend(df)
    assert out["available"] is True
    assert out["score"] is not None and 0 <= out["score"] <= 100
    # 近 5 日均值>0 且上涨占比 4/5=0.8 -> 应拿到较高分（>=40）
    assert out["score"] >= 40


def test_sector_trend_close_present_still_uses_ma():
    from app.sector_engine.engine import SectorEngine

    eng = SectorEngine()
    # 单调上行 close（>=20 点使 MA20 可算），验证仍走 MA 分支（不被 change_percent 兜底干扰）
    closes = list(range(100, 125))
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-07-01", periods=len(closes), freq="D"),
        "close": closes,
        "change_percent": [0.0] * len(closes),
    })
    out = eng.evaluate_sector_trend(df)
    assert out["available"] is True
    assert out["score"] is not None
    assert out["supporting"].get("above_ma20") is True
