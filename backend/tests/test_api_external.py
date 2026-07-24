"""外部 skill 数据源端点测试（Phase C / P2·P3·P5）。

不依赖真实外网：monkeypatch external_data.collect_* 为可控返回值，
验证「正常 / 降级（available=False）」两种路径均不 500 且字段正确。
降级路径对应：npx westock-data 超时 / 东财网络不可用 / 盈米 CLI 未安装。
"""
import pytest

from app.services import external_data as ext


@pytest.fixture()
def client(api_client):
    return api_client


# ---- P3 板块异动（腾讯自选股） ---- #
def test_sectors_movement_ok(client, monkeypatch):
    monkeypatch.setattr(
        ext,
        "collect_sector_movement",
        lambda: {
            "available": True,
            "source": "腾讯自选股 westock-data",
            "industry": [{"name": "银行", "changePct": 1.2, "changePct5d": 0.5, "leadStock": "招商银行"}],
            "concept": [{"name": "AI", "changePct": 3.4, "changePct5d": 2.1, "leadStock": "寒武纪"}],
            "fund_flow": [{"name": "银行", "changePct": 1.2, "mainNetInflow": 123456, "mainNetInflow5d": 99999, "upDownRatio": "3:1"}],
        },
    )
    r = client.get("/api/external/sectors/movement")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert d["industry"][0]["name"] == "银行"
    assert d["concept"][0]["changePct"] == 3.4
    assert d["fund_flow"][0]["mainNetInflow"] == 123456


def test_sectors_movement_degraded(client, monkeypatch):
    def boom():
        raise RuntimeError("npx timeout")

    monkeypatch.setattr(ext, "collect_sector_movement", boom)
    r = client.get("/api/external/sectors/movement")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is False
    assert d["industry"] == []
    assert d["concept"] == []
    assert d["fund_flow"] == []


# ---- P5 当日新闻（东财全球资讯） ---- #
def test_news_ok(client, monkeypatch):
    monkeypatch.setattr(
        ext,
        "collect_news",
        lambda limit=30: {
            "available": True,
            "source": "东方财富全球资讯",
            "items": [{"time": "2026-07-24 14:30:00", "title": "央行降准", "summary": "释放长期资金"}],
        },
    )
    r = client.get("/api/external/news?limit=10")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert d["items"][0]["title"] == "央行降准"


def test_news_degraded(client, monkeypatch):
    monkeypatch.setattr(
        ext,
        "collect_news",
        lambda limit=30: {"available": False, "reason": "网络不可用", "items": []},
    )
    r = client.get("/api/external/news")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is False
    assert d["items"] == []


# ---- P2 场外基金（盈米） ---- #
def test_offexchange_ok(client, monkeypatch):
    monkeypatch.setattr(
        ext,
        "collect_offexchange_funds",
        lambda keyword="ETF", limit=10: {
            "available": True,
            "source": "盈米 yingmi-skill-cli",
            "items": [{"code": "000001", "name": "华夏成长", "type": "混合型", "change_percent": 1.5, "nav": 1.2345}],
        },
    )
    r = client.get("/api/external/offexchange?keyword=ETF&limit=5")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert d["items"][0]["code"] == "000001"
    assert d["items"][0]["nav"] == 1.2345


def test_offexchange_unavailable(client, monkeypatch):
    monkeypatch.setattr(
        ext,
        "collect_offexchange_funds",
        lambda keyword="ETF", limit=10: {
            "available": False,
            "reason": "盈米 CLI 未安装或未授权",
            "items": [],
        },
    )
    r = client.get("/api/external/offexchange")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is False
    assert d["reason"] == "盈米 CLI 未安装或未授权"
