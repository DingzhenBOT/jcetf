"""意见端点测试（P4）：/api/opinions/{etf}。

临时库：510300 有 post_close + midday 两条意见；510050 无信号/意见；999999 非映射。
"""


def test_opinions_for_etf_returns_sorted(api_client):
    r = api_client.get("/api/opinions/510300")
    assert r.status_code == 200
    body = r.json()
    assert body["etf_code"] == "510300"
    assert len(body["items"]) == 2
    # 按 generated_at desc：post_close(15:10) 在前
    assert body["items"][0]["phase"] == "post_close"
    assert body["items"][0]["content"].startswith("沪深300ETF")


def test_opinions_phase_filter(api_client):
    r = api_client.get("/api/opinions/510300?phase=midday")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["phase"] == "midday"


def test_opinions_invalid_phase_422(api_client):
    r = api_client.get("/api/opinions/510300?phase=bogus")
    assert r.status_code == 422


def test_opinions_unknown_etf_404(api_client):
    r = api_client.get("/api/opinions/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_opinions_etf_without_opinions_empty_list(api_client):
    # 510050 是生效映射但无意见 -> 200 + 空列表（非 404）
    r = api_client.get("/api/opinions/510050")
    assert r.status_code == 200
    assert r.json()["items"] == []
