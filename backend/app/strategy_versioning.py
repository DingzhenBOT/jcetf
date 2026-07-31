"""策略版本化（DESIGN §9.3，P1 落基建，P3 复用）。

strategy_hash = SHA256(规则JSON + 参数JSON)，规范化 JSON 避免字段顺序不同生成不同版本。
内容变化必然产生新版本号；旧版本禁止改写（由 strategy_version 表唯一约束 + 写保护保证）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Tuple

from app.config import Settings
from app.db.models.mapping import StrategyVersion


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


def _strategy_params(settings: Settings) -> dict:
    return {
        "composite_weights": settings.strategy.composite_weights,
        "thresholds": settings.strategy.thresholds,
        "risk_filter": settings.strategy.risk_filter,
    }


def strategy_version_for_rules(settings: Settings, rules: dict) -> Tuple[str, str]:
    """按指定冻结规则计算可执行版本，不写数据库。"""
    strategy_hash = compute_strategy_hash(_strategy_params(settings), rules)
    return build_version_string(settings.strategy.version, strategy_hash), strategy_hash


def current_strategy_version(settings: Settings) -> Tuple[str, str]:
    """基于当前配置计算 (version, strategy_hash)。

    P1 仅含 params（权重/阈值/风险过滤），rules 留空 {}；P3 填充实际规则后 hash 变化 -> 新版本。
    """
    return strategy_version_for_rules(settings, {})


def mint_strategy_version(session, settings: Settings, rules: dict) -> str:
    """铸造（或复用）不可覆盖的策略版本；返回 version 字符串。

    - params 取 strategy.composite_weights/thresholds/risk_filter；rules 为冻结规则字典（RULES_V1）。
    - 计算 hash；若库中已存在同 version（PK）或同 strategy_hash（唯一约束）则复用，绝不 UPDATE。
    - P1 已 seed 的占位版本（rules_json={}，hash 不同）不会被改写：本函数插入一条**全新的**版本行，
      旧版本保持不可覆盖。
    """
    from sqlalchemy.orm import Session

    params = _strategy_params(settings)
    version, strategy_hash = strategy_version_for_rules(settings, rules)

    if not isinstance(session, Session):
        raise TypeError("mint_strategy_version requires an active SQLAlchemy Session")

    existing = session.get(StrategyVersion, version)
    if existing is not None:
        return version  # 幂等：已存在则复用，禁止覆盖

    session.add(
        StrategyVersion(
            version=version,
            strategy_hash=strategy_hash,
            name=f"rules-{rules.get('version', 'unknown')}",
            description=rules.get("description", "deterministic strategy rules"),
            params_json=params,
            rules_json=rules,
        )
    )
    return version
