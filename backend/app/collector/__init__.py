"""采集层包（DESIGN §3.1 / P2）：normalize（列映射）+ collector（编排）。

- normalize：把 AkShare 中文列 DataFrame 统一映射为 market_quote / market_breadth 的字典形态，
  缺失字段写 None（由 data_quality 标记），业务代码不感知来源。
- collector：调用 provider -> normalize -> 质量评估 -> 切源标记 -> 幂等入库 -> 数据源状态。
"""
from __future__ import annotations
