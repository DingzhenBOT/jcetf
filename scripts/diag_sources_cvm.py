#!/usr/bin/env python3
"""CVM 数据源连通性自测（C19 板块数据缺失排查用）。

背景：CVM 上板块历史/资金流当前完全采不到——em(push2) 被 RST 拦截、ths 返回空/解析报错、
get_sector_fund_flow_history 仅接了 em（未接 ths）。需先确认 CVM 究竟能连通哪个源、以及东财
datacenter-web 实际使用的报表名（报表名动态混淆，需从页面 XHR 抓取），再据此写适配器。

用法（在 CVM 上）：
    backend/venv/bin/python scripts/diag_sources_cvm.py

输出各候选主机的可达性 + 东财板块资金流页面的真实 reportName，便于 agent 据此开发。
"""
import re
import sys
import urllib.request

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def probe(label, url):
    try:
        raw = _get(url)
        # 能拿到文本即视为可达（即便业务返回空/报错）
        snippet = raw[:120].replace("\n", " ")
        print(f"  [OK ] {label}: reachable, body_len={len(raw)} :: {snippet}")
        return raw
    except Exception as e:  # noqa: BLE001
        print(f"  [BLOCK] {label}: {type(e).__name__}: {str(e)[:80]}")
        return None


def main() -> None:
    print("=== CVM 数据源连通性自测 ===\n")
    print("[1] 东财行情类主机（akshare em 底层，已知 CVM 被 RST）")
    probe("push2.eastmoney.com", "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&fs=m:90+t:2&fields=f12")
    probe("push2his.eastmoney.com", "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.BK1036&fields1=f1&fields2=f51&klt=101&fqt=0&beg=20260701&end=20500101")

    print("\n[2] 东财 datacenter-web 主机（沙箱实测可达，报表名需确认）")
    probe("datacenter-web RPT_DMSK_TS_STOCKBKDATA",
          "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DMSK_TS_STOCKBKDATA&columns=BOARD_CODE&pageSize=1&pageNumber=1")

    print("\n[3] 同花顺 ths 主机（沙箱实测解析报错，CVM 返回空）")
    probe("www.10jqka.com.cn", "https://www.10jqka.com.cn/")

    print("\n[4] 已知可用源（对照）")
    probe("qt.gtimg.cn (腾讯实时)", "https://qt.gtimg.cn/q=sh000300")
    probe("web.ifzq.gtimg.cn (腾讯分时)", "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh510300")

    print("\n[5] 抓取东财板块资金流页面真实 reportName（datacenter-web 报表名动态混淆，需从页面 XHR 提取）")
    for page in ["https://data.eastmoney.com/bkfenxi/", "https://data.eastmoney.com/bkzj.html"]:
        raw = probe("page " + page, page)
        if raw:
            names = sorted(set(re.findall(r"reportName['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", raw)))
            if names:
                print(f"     发现 reportName: {names}")
            else:
                print("     未在页面 HTML 中直接发现 reportName（可能在懒加载 JS 中，需浏览器抓 XHR）")

    print("\n=== 自测结束 ===")
    print("把以上输出贴回给 agent；若 datacenter-web 可达且能提取到 reportName，agent 据此开发板块资金流适配器。")


if __name__ == "__main__":
    main()
