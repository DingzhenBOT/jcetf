"""东财请求头补丁单测（P9+）。

- _em_headers_for：仅 eastmoney URL 返回头，新浪/同花顺不受影响。
- install_em_headers_patch：安装后，对任意 eastmoney URL 的 requests 调用注入 Referer/UA；
  用本地 mock 验证注入逻辑（不触网）。
"""
import requests

from app.data_provider.akshare_adapter import _em_headers_for, install_em_headers_patch


def test_em_headers_for_only_eastmoney():
    hdr = _em_headers_for("https://push2his.eastmoney.com/api/qt/stock/kline/get")
    assert hdr is not None
    assert hdr["Referer"] == "https://quote.eastmoney.com/"
    assert hdr["User-Agent"]  # 非空 UA
    assert _em_headers_for("https://hq.sinajs.cn/list=sh000300") is None
    assert _em_headers_for("https://push2.eastmoney.com/foo") is not None  # 仍含 eastmoney.com
    assert _em_headers_for("https://datacenter.eastmoney.com/api/") is not None


def test_patch_injects_headers_for_eastmoney_only(monkeypatch):
    # 用本地假 Session.request 记录调用，验证补丁只给 eastmoney URL 加头
    captured = {}

    class FakeResp:
        pass

    def fake_request(self, method, url, *args, **kwargs):
        captured["url"] = url
        captured["headers"] = dict(kwargs.get("headers") or {})
        return FakeResp()

    monkeypatch.setattr(requests.Session, "request", fake_request)
    install_em_headers_patch()  # 幂等，重复调用安全

    s = requests.Session()
    # 东财 URL -> 应注入 Referer/UA
    s.get("https://push2his.eastmoney.com/api/qt/stock/kline/get", params={"a": 1})
    assert captured["url"].startswith("https://push2his.eastmoney.com")
    assert captured["headers"].get("Referer") == "https://quote.eastmoney.com/"
    assert "User-Agent" in captured["headers"]

    # 新浪 URL -> 不应加东财头
    s.get("https://hq.sinajs.cn/list=sh000300")
    assert captured["headers"] == {} or "Referer" not in captured["headers"]
