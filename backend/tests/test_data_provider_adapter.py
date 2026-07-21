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
