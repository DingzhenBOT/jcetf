"""AkShareAdapter 历史接口单测（P3 修复）。

- _to_sina_symbol：数字代码 -> sh/sz 前缀（指数/ETF 规则不同）。
- _history_source_map：em 注入 start/end；sina/tx 仅 symbol（sh/sz 前缀），不注入起止参数。
- 这些修复确保 em 不可达时，指数/ETF 历史能降级到新浪且不因多余参数 TypeError。
"""
from app.config import get_settings
from app.data_provider.akshare_adapter import AkShareAdapter


def _adapter() -> AkShareAdapter:
    return AkShareAdapter(get_settings(force_reload=True))


def test_to_sina_symbol_index():
    a = _adapter()
    # 上交所指数（0/6 前缀）-> sh
    assert a._to_sina_symbol("000300", "index") == "sh000300"
    assert a._to_sina_symbol("000001", "index") == "sh000001"
    assert a._to_sina_symbol("000016", "index") == "sh000016"
    assert a._to_sina_symbol("000688", "index") == "sh000688"
    assert a._to_sina_symbol("000905", "index") == "sh000905"
    # 深交所指数（3 前缀）-> sz
    assert a._to_sina_symbol("399001", "index") == "sz399001"
    assert a._to_sina_symbol("399006", "index") == "sz399006"
    # 已带前缀则原样
    assert a._to_sina_symbol("sh000300", "index") == "sh000300"
    assert a._to_sina_symbol("SZ399001", "index") == "sz399001"


def test_to_sina_symbol_etf():
    a = _adapter()
    # 上交所 ETF（5 前缀）-> sh
    assert a._to_sina_symbol("510300", "etf") == "sh510300"
    assert a._to_sina_symbol("588000", "etf") == "sh588000"
    # 深交所 ETF（1 前缀）-> sz
    assert a._to_sina_symbol("159915", "etf") == "sz159915"
    # 场外联接（1/0 前缀）-> sz（新浪无历史，预期 FAILED 不致命）
    assert a._to_sina_symbol("110020", "etf") == "sz110020"
    assert a._to_sina_symbol("000008", "etf") == "sz000008"
    # 已带前缀则原样
    assert a._to_sina_symbol("sh510300", "etf") == "sh510300"


def test_history_source_map_etf_per_source_kwargs():
    a = _adapter()
    m = a._history_source_map(a._ETF_HIST, "etf", "510300", "20240101", "20240131")
    # em：注入 symbol + start/end + period/adjust
    em_func, em_kw = m["em"]
    assert em_func == "fund_etf_hist_em"
    assert em_kw["symbol"] == "510300"
    assert em_kw["start_date"] == "20240101" and em_kw["end_date"] == "20240131"
    assert em_kw["period"] == "daily" and em_kw["adjust"] == "qfq"
    # sina：仅 symbol（已转 sh/sz），无 start/end（否则 fund_etf_hist_sina TypeError）
    sina_func, sina_kw = m["sina"]
    assert sina_func == "fund_etf_hist_sina"
    assert sina_kw == {"symbol": "sh510300"}


def test_history_source_map_index_per_source_kwargs():
    a = _adapter()
    m = a._history_source_map(a._INDEX_HIST, "index", "000300", "20240101", "20240131")
    em_func, em_kw = m["em"]
    assert em_func == "stock_zh_index_daily_em"
    # em 指数必须 sh/sz 前缀（裸码 stock_zh_index_daily_em 静默返回空）
    assert em_kw["symbol"] == "sh000300"
    assert em_kw["start_date"] == "20240101"
    # sina：仅 symbol，已转 sh/sz，无 start/end
    sina_func, sina_kw = m["sina"]
    assert sina_func == "stock_zh_index_daily"
    assert sina_kw == {"symbol": "sh000300"}
    # tx：仅 symbol，已转 sh/sz，无 start/end
    tx_func, tx_kw = m["tx"]
    assert tx_func == "stock_zh_index_daily_tx"
    assert tx_kw == {"symbol": "sh000300"}


def test_history_symbol_format_per_source():
    a = _adapter()
    # 指数：em/sina/tx 都需 sh/sz 前缀
    assert a._history_symbol("index", "em", "000300") == "sh000300"
    assert a._history_symbol("index", "sina", "000300") == "sh000300"
    assert a._history_symbol("index", "tx", "399001") == "sz399001"
    # ETF：em 传裸码（fund_etf_hist_em 内部查市场）；sina/tx 需 sh/sz 前缀
    assert a._history_symbol("etf", "em", "510300") == "510300"
    assert a._history_symbol("etf", "sina", "510300") == "sh510300"
    assert a._history_symbol("etf", "tx", "510300") == "sh510300"


def test_bk_to_ths_mapping_resolves_industry_and_concept():
    a = _adapter()
    # 行业板：半导体 / 证券(券商) / 银行 / 白酒 / 光伏设备
    assert a._bk_to_ths("BK1036") == ("industry", "半导体")
    assert a._bk_to_ths("BK0473") == ("industry", "证券")
    assert a._bk_to_ths("BK0475") == ("industry", "银行")
    assert a._bk_to_ths("BK0471") == ("industry", "白酒")
    assert a._bk_to_ths("BK1035") == ("industry", "光伏设备")
    # 概念板：军工 / 新能源汽车 / 5G
    assert a._bk_to_ths("BK0481") == ("concept", "军工")
    assert a._bk_to_ths("BK0900") == ("concept", "新能源汽车")
    assert a._bk_to_ths("BK0999") == ("concept", "5G")


def test_bk_to_ths_mapping_none_for_unmapped_sectors():
    a = _adapter()
    # 医药/消费在 THS 无单一聚合板 -> None（调用方跳过 ths 源，降级 D4）
    assert a._bk_to_ths("BK0465") is None
    assert a._bk_to_ths("BK0438") is None
    # 未知 BK 也返回 None（不臆造映射）
    assert a._bk_to_ths("BK9999") is None


def test_get_sector_history_builds_ths_source_only_for_mapped(monkeypatch):
    """get_sector_history 为 ths 源解析 BK -> 板块名；无映射则跳过 ths 源（不触网）。"""
    a = _adapter()
    seen = {}

    def _fake_call(capability, source_map):
        seen["map"] = dict(source_map)
        import pandas as pd
        df = pd.DataFrame([{"日期": "2024-01-02", "开盘价": 1, "最高价": 2,
                            "最低价": 0.5, "收盘价": 1.5, "成交量": 10, "成交额": 100}])
        return df, "ths"

    monkeypatch.setattr(a, "_call", _fake_call)
    monkeypatch.setattr(a, "_ordered_sources", lambda: ["ths"])

    # 半导体 -> 行业板 stock_board_industry_index_ths
    df = a.get_sector_history("BK1036", "20240101", "20240131")
    assert seen["map"]["ths"][0] == "stock_board_industry_index_ths"
    assert seen["map"]["ths"][1]["symbol"] == "半导体"
    assert seen["map"]["ths"][1]["start_date"] == "20240101"
    assert len(df) == 1

    # 军工 -> 概念板 stock_board_concept_index_ths
    df = a.get_sector_history("BK0481", "20240101", "20240131")
    assert seen["map"]["ths"][0] == "stock_board_concept_index_ths"
    assert seen["map"]["ths"][1]["symbol"] == "军工"

    # 医药 -> 无 THS 映射，ths 源被跳过；仅 ths 时 source_map 为空 -> DataSourceError
    from app.errors import DataSourceError
    try:
        a.get_sector_history("BK0465", "20240101", "20240131")
        assert False, "expected DataSourceError when ths unmapped and only ths source"
    except DataSourceError:
        pass


def test_get_sector_history_em_source_uses_bk_code(monkeypatch):
    """em 源直接用 BK 代码（stock_board_industry_hist_em），不经 THS 解析。"""
    a = _adapter()
    seen = {}

    def _fake_call(capability, source_map):
        seen["map"] = dict(source_map)
        import pandas as pd
        return pd.DataFrame([{"日期": "2024-01-02", "开盘": 1, "最高": 2,
                              "最低": 0.5, "收盘": 1.5, "成交量": 10, "成交额": 100,
                              "涨跌幅": 1.0, "换手率": 0.5}]), "em"

    monkeypatch.setattr(a, "_call", _fake_call)
    monkeypatch.setattr(a, "_ordered_sources", lambda: ["em"])
    a.get_sector_history("BK1036", "20240101", "20240131")
    assert seen["map"]["em"][0] == "stock_board_industry_hist_em"
    assert seen["map"]["em"][1]["symbol"] == "BK1036"
    assert seen["map"]["em"][1]["period"] == "daily"
