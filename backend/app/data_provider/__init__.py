"""数据源适配器包（DESIGN §3.1）。

业务代码只依赖 `BaseDataProvider` 抽象；具体实现通过 `build_provider` 按配置构造。
- `real` 模式 -> AkShareAdapter（多源降级）。
- `mock` 模式 -> 后续阶段实现；当前未实现（避免无来源时悄悄降级 Mock，符合 DESIGN §0）。
"""
from __future__ import annotations

from app.config import Settings
from app.data_provider.akshare_adapter import AkShareAdapter, install_em_headers_patch
from app.data_provider.base import BaseDataProvider


def build_provider(settings: Settings) -> BaseDataProvider:
    """按配置构造数据源适配器。"""
    if settings.data_source.mode == "real":
        # 东财 kline 接口需 Referer/UA 头，否则返回空（腾讯云网络通但被应用层拒）。
        # 在构造适配器前安装幂等补丁（仅作用于 eastmoney URL）。
        install_em_headers_patch()
        return AkShareAdapter(settings)
    # Mock 适配器在第二阶段实现；当前仅允许 real（DESIGN §0 禁止生产/未实现时降级 Mock）
    raise NotImplementedError(
        f"data_source.mode={settings.data_source.mode!r} not supported yet; "
        "set data_source.mode=real (mock provider lands in a later phase)"
    )


__all__ = ["BaseDataProvider", "AkShareAdapter", "build_provider"]
