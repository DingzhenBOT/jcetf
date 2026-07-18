"""策略版本化（DESIGN §9.3，P1 落基建，P3 复用）。

strategy_hash = SHA256(规则JSON + 参数JSON)，规范化 JSON 避免字段顺序不同生成不同版本。
内容变化必然产生新版本号；旧版本禁止改写（由 strategy_version 表唯一约束 + 写保护保证）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Tuple

from app.config import Settings


def compute_strategy_hash(params: dict, rules: dict) -> str:
    """规范化 JSON -> SHA256 十六进制。"""
    payload = json.dumps(
        {"params": params, "rules": rules},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_version_string(base: str, strategy_hash: str) -> str:
    """版本号形如 v1.0.0-<hash前6位>。"""
    return f"{base}-{strategy_hash[:6]}"


def current_strategy_version(settings: Settings) -> Tuple[str, str]:
    """基于当前配置计算 (version, strategy_hash)。

    P1 仅含 params（权重/阈值/风险过滤），rules 留空 {}；P3 填充实际规则后 hash 变化 -> 新版本。
    """
    params = {
        "composite_weights": settings.strategy.composite_weights,
        "thresholds": settings.strategy.thresholds,
        "risk_filter": settings.strategy.risk_filter,
    }
    strategy_hash = compute_strategy_hash(params, {})
    version = build_version_string(settings.strategy.version, strategy_hash)
    return version, strategy_hash
