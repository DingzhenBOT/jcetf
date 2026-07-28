"""板块名 -> BK 代码映射（westock-data 腾讯自选股只返回板块名，引擎按 BK 关联）。

背景：CVM 上东财 push2/push2his 被 RST、ths 返回空/解析错、akshare 新版已删 sina 板块函数，
唯一 CVM 稳定可用的板块源是腾讯自选股 westock-data `sector ranking`。但该接口只返回
「行业/概念涨幅 + 资金流入 TOP 榜」（异动榜，非全量历史），且给的是**板块名**而非 BK 代码。

故本模块把 westock 板块名解析为跟踪的 BK 代码，仅当日活跃出现在榜上的板块入库；
未出现/未匹配的板块由引擎优雅降级（D4）。

映射以「规范名 + 常见别名 + 子串兜底」解析；取值依据
app/data_provider/akshare_adapter._BK_TO_THS 规范名 + 东财/腾讯板块命名习惯。
未匹配板块名由调用方记日志，便于后续补全。
"""
from __future__ import annotations

# 跟踪板块 BK 代码 -> 候选板块名（规范名 + 常见别名，按数组顺序优先级）。
SECTOR_NAME_ALIASES: dict[str, list[str]] = {
    "BK1036": ["半导体"],
    "BK0473": ["证券", "券商"],
    "BK0475": ["银行"],
    "BK0481": ["军工"],
    "BK0900": ["新能源汽车", "新能源车"],
    "BK0999": ["5G", "通信设备", "5G概念"],
    "BK1035": ["光伏设备", "光伏", "光伏产业"],
    "BK0471": ["白酒"],
    "BK0465": ["医药", "化学制药", "中药", "医疗器械", "医疗服务", "生物制品"],
    "BK0438": ["消费", "食品饮料", "饮料制造", "酿酒", "食品加工"],
}


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def resolve_sector_bk(name: str, sector_codes: set[str]) -> str | None:
    """把 westock 板块名解析为跟踪的 BK 代码；无法解析返回 None。

    sector_codes：本次实际跟踪的 BK 代码集合（映射仅在其内解析，避免误匹配无关板块）。
    解析顺序：① 别名精确匹配；② 子串兜底（规范名/别名是 westock 名子串，或反之）。
    """
    n = _norm(name)
    if not n:
        return None
    # 1) 别名精确匹配（仅限跟踪板块）
    for bk, aliases in SECTOR_NAME_ALIASES.items():
        if bk not in sector_codes:
            continue
        for a in aliases:
            if _norm(a) == n:
                return bk
    # 2) 子串兜底：规范名/别名是 westock 名子串，或反之
    for bk, aliases in SECTOR_NAME_ALIASES.items():
        if bk not in sector_codes:
            continue
        for a in aliases:
            na = _norm(a)
            if na and (na in n or n in na):
                return bk
    return None
