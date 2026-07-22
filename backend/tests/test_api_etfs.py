"""ETF 列表端点测试（P4）：/api/etfs。

临时库播种 3 支 ETF（510050 无信号）-> latest_signal 应为 null。
"""


def test_etfs_list_count_and_fields(api_client):
    r = api_client.get("/api/etfs")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    codes = {e["etf_code"] for e in body}
    assert codes == {"510300", "510500", "510050"}


def test_etfs_latest_signal_present_or_null(api_client):
    r = api_client.get("/api/etfs")
    by_code = {e["etf_code"]: e for e in r.json()}
    # 有信号的 etf
    assert by_code["510300"]["latest_signal"] is not None
    assert by_code["510300"]["latest_signal"]["signal_type_text"] == "市场风险大，先观望"
    assert by_code["510500"]["latest_signal"]["signal_type"] == "NO_PARTICIPATE"
    # 无信号的 etf -> null
    assert by_code["510050"]["latest_signal"] is None


def test_etfs_mapping_fields_exposed(api_client):
    r = api_client.get("/api/etfs")
    e = next(x for x in r.json() if x["etf_code"] == "510300")
    assert e["etf_name"] == "沪深300ETF"
    assert e["category"] == "宽基"
    assert e["related_index_code"] == "000300"
    assert e["related_sector_codes"] == ["BK0465"]
