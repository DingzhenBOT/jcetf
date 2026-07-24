"""外部 skill 数据源接入层（Phase C / P2·P3·P5）。

数据源（已与用户确认，弃用平安）：
- 板块异动 P3：腾讯自选股 `westock-data sector ranking`（npx，无 key，腾讯云可用）
- 当日新闻 P5：东财全球资讯 `np-weblist.eastmoney.com`（a-stock-data 提供，无 key）
- 场外基金 P2：盈米 `yingmi-skill-cli` MCP（需在 CVM 安装并授权；未配置时优雅降级）

所有采集函数对失败做可控降级：返回 dict 带 `available` 标记，由端点决定降级文案，
绝不抛出未捕获异常导致 500。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

WESTOCK_CMD = ["npx", "-y", "westock-data-skillhub@1.0.5"]
EM_GLOBAL_NEWS_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"


# --------------------------------------------------------------------------- #
# 通用：执行外部命令 / HTTP
# --------------------------------------------------------------------------- #
def _run_cmd(cmd: List[str], timeout: int = 120) -> str:
    """执行命令返回 stdout；失败抛 RuntimeError（由调用方捕获降级）。"""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"cmd failed({proc.returncode}): {proc.stderr[:300]}")
    return proc.stdout


def _get_json(url: str, params: Dict[str, str], headers: Dict[str, str], timeout: int = 10) -> Any:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# P3 板块异动：腾讯自选股 westock-data sector ranking
# --------------------------------------------------------------------------- #
def collect_sector_movement() -> Dict[str, Any]:
    """板块涨幅/概念涨幅/行业资金流入排名。"""
    out = _run_cmd([*WESTOCK_CMD, "sector", "ranking"], timeout=150)
    industry, concept, fund_flow = _parse_sector_ranking(out)
    return {
        "available": True,
        "source": "腾讯自选股 westock-data",
        "industry": industry,
        "concept": concept,
        "fund_flow": fund_flow,
    }


def _parse_sector_ranking(text: str) -> tuple[List[dict], List[dict], List[dict]]:
    """解析 westock-data `sector ranking` 的 markdown 三段表格。"""
    industry: List[dict] = []
    concept: List[dict] = []
    fund_flow: List[dict] = []
    blocks = _split_markdown_blocks(text)
    for title, body in blocks:
        rows = _parse_md_table(body)
        if "行业板块涨幅排名" in title:
            industry = rows
        elif "概念板块涨幅排名" in title:
            concept = rows
        elif "行业资金流入" in title:
            fund_flow = rows
    return industry, concept, fund_flow


def _split_markdown_blocks(text: str) -> List[tuple[str, str]]:
    """按 **标题** 切分 markdown 文本为 (标题, 正文) 列表。"""
    blocks: List[tuple[str, str]] = []
    cur_title = ""
    cur_body: List[str] = []
    for line in text.splitlines():
        if line.strip().startswith("**") and line.strip().endswith("**"):
            if cur_title or cur_body:
                blocks.append((cur_title, "\n".join(cur_body)))
            cur_title = line.strip().strip("*").strip()
            cur_body = []
        else:
            cur_body.append(line)
    if cur_title or cur_body:
        blocks.append((cur_title, "\n".join(cur_body)))
    return blocks


def _parse_md_table(body: str) -> List[dict]:
    """解析单段 markdown 表格为 dict 列表（首行为表头）。"""
    lines = [l for l in body.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return []
    headers = [h.strip() for h in lines[0].strip().strip("|").split("|")]
    rows: List[dict] = []
    for l in lines[2:]:  # 跳过分隔行
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        row = {}
        for h, c in zip(headers, cells):
            row[h] = _coerce(c)
        rows.append(row)
    return rows


def _coerce(v: str) -> Any:
    v = v.strip()
    try:
        f = float(v)
        return f
    except ValueError:
        return v


# --------------------------------------------------------------------------- #
# P5 当日新闻：东财全球资讯（7x24，无 key）
# --------------------------------------------------------------------------- #
def collect_news(limit: int = 30) -> Dict[str, Any]:
    """东方财富全球财经资讯（7x24 滚动）。返回标题+摘要+时间。"""
    params = {
        "client": "web",
        "biz": "web_724",
        "fastColumn": "102",
        "sortEnd": "",
        "pageSize": str(limit),
        "req_trace": "00000000-0000-0000-0000-000000000000",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://kuaixun.eastmoney.com/",
    }
    try:
        d = _get_json(EM_GLOBAL_NEWS_URL, params, headers, timeout=10)
    except Exception as e:  # 网络/解析失败 -> 降级
        return {"available": False, "reason": f"资讯源暂不可用：{e}", "items": []}
    items: List[dict] = []
    for it in d.get("data", {}).get("fastNewsList", [])[:limit]:
        items.append(
            {
                "time": it.get("showTime", ""),
                "title": it.get("title", ""),
                "summary": (it.get("summary") or "")[:200],
            }
        )
    return {"available": True, "source": "东方财富全球资讯", "items": items}


# --------------------------------------------------------------------------- #
# P2 场外基金：盈米 yingmi-skill-cli（需 CVM 安装并授权）
# --------------------------------------------------------------------------- #
def collect_offexchange_funds(keyword: str = "ETF", limit: int = 10) -> Dict[str, Any]:
    """场外基金检索（盈米 SearchFunds）。未安装/未授权时优雅降级。"""
    if not shutil.which("yingmi-skill-cli"):
        return {
            "available": False,
            "reason": "盈米 CLI（yingmi-skill-cli）未安装或未授权；请在 CVM 执行其 CLI 前置检查并初始化。",
            "items": [],
        }
    try:
        out = _run_cmd(
            [
                "yingmi-skill-cli",
                "mcp",
                "call",
                "SearchFunds",
                "--input",
                json.dumps({"keyword": keyword, "size": limit}),
            ],
            timeout=60,
        )
        data = json.loads(out)
        items = _extract_yingmi_funds(data)
        return {"available": True, "source": "盈米 yingmi-skill-cli", "items": items}
    except Exception as e:
        return {"available": False, "reason": f"盈米调用失败：{e}", "items": []}


def _extract_yingmi_funds(data: Any) -> List[dict]:
    """从盈米 SearchFunds 返回中抽取基金列表（兼容常见嵌套结构）。"""
    if isinstance(data, dict):
        # 常见：{content:[{text:...}]} 或 {result:...} 或 {data:...}
        for key in ("content", "result", "data", "funds"):
            if key in data and isinstance(data[key], list):
                return [_normalize_fund(f) for f in data[key]]
        if "text" in data:  # MCP content 包了一层
            try:
                return _extract_yingmi_funds(json.loads(data["text"]))
            except Exception:
                return []
    if isinstance(data, list):
        return [_normalize_fund(f) for f in data]
    return []


def _normalize_fund(f: Any) -> dict:
    if isinstance(f, str):
        try:
            f = json.loads(f)
        except Exception:
            return {"raw": f}
    if not isinstance(f, dict):
        return {"raw": str(f)}
    return {
        "code": f.get("code") or f.get("fundCode") or f.get("fdCode"),
        "name": f.get("name") or f.get("fundName") or f.get("fdName"),
        "type": f.get("type") or f.get("fundType"),
        "change_percent": f.get("changePercent") or f.get("dayGrowth") or f.get("navChange"),
        "nav": f.get("nav") or f.get("unitNav"),
    }
