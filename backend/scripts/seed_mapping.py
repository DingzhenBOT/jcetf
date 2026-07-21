"""ETF 映射种子（P3 自测 / 初始化）。

幂等：每支 ETF 按 (etf_code, mapping_version) upsert；重复运行仅确认行存在。
valid_from 设为远古日期，确保对任何 as_of（含历史回填、单测）均生效；回测按 valid_from/valid_to 取当时映射。

使用 em 板块代码（生产身份）；沙箱/用户服务器若 em 不可达、板块 BAR 缺失，引擎按 D4 优雅降级
（sector_trend/fund_flow -> None，综合分重归一化、降置信），不会崩溃。

用法：
  python3.11 -m scripts.seed_mapping
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from app.config import get_settings
from app.db import init_db, make_engine, session_scope
from app.logging_conf import get_logger, setup_logging
from app.repository import mapping_repo

# 种子清单：etf_code, etf_name, related_index_code, related_sector_codes, category
SEED = [
    ("510300", "沪深300ETF", "000300", [], "宽基"),
    ("510500", "中证500ETF", "000905", [], "宽基"),
    ("510050", "上证50ETF", "000016", [], "宽基"),
    ("159915", "创业板ETF", "399006", [], "宽基", "场内"),
    ("588000", "科创50ETF", "000688", [], "宽基", "场内"),
    ("512010", "医药ETF", "000300", ["BK0465"], "医药", "场内"),
    ("512660", "军工ETF", "000300", ["BK0481"], "军工", "场内"),
    ("515030", "新能源车ETF", "000300", ["BK0900"], "新能源车", "场内"),
    ("512760", "芯片ETF", "000300", ["BK1036"], "半导体", "场内"),
    ("515050", "5G ETF", "000300", ["BK0999"], "5G", "场内"),
    ("512000", "券商ETF", "000300", ["BK0473"], "证券", "场内"),
    ("512800", "银行ETF", "000300", ["BK0475"], "银行", "场内"),
    ("159928", "消费ETF", "000300", ["BK0438"], "消费", "场内"),
    ("512690", "酒ETF", "000300", ["BK0471"], "白酒", "场内"),
    ("515790", "光伏ETF", "000300", ["BK1035"], "光伏", "场内"),
    ("512880", "证券ETF", "000300", ["BK0473"], "证券", "场内"),
    # 场外联接基金（示例，用于区分场内/场外）
    ("110020", "沪深300ETF联接A", "000300", [], "宽基", "场外"),
    ("000008", "中证500ETF联接A", "000905", [], "宽基", "场外"),
    ("110003", "易方达上证50联接A", "000016", [], "宽基", "场外"),
]

# 远古 valid_from：对任何 as_of 生效（历史回填/回测/单测均可用）
VALID_FROM = date(2000, 1, 1)
MAPPING_VERSION = "v1"


def main() -> int:
    ap = argparse.ArgumentParser(description="ETF 映射种子（P3）")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    settings = get_settings(config_path=args.config)
    setup_logging(settings)
    settings.ensure_dirs()
    log = get_logger("seed_mapping")

    eng = make_engine(settings)
    init_db(eng, settings)

    with session_scope(eng) as session:
        for code, name, idx, sectors, cat, listing in SEED:
            mapping_repo.upsert_mapping(
                session,
                etf_code=code,
                etf_name=name,
                related_sector_codes=sectors,
                related_index_code=idx,
                category=cat,
                listing=listing,
                mapping_version=MAPPING_VERSION,
                valid_from=VALID_FROM,
                valid_to=None,
                notes="P3 seed",
            )
        count = len(mapping_repo.get_active_mappings(session))
    eng.dispose()

    log.info("seed done", extra={"mappings": count})
    print(f"seeded {count} active ETF mappings (version={MAPPING_VERSION})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
