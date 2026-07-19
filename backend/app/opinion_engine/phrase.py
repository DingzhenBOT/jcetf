"""措辞客户端抽象（P3，D1）。

DESIGN §0：「LLM 只润色文本，不判断」。P3 默认仅用确定性模板（TemplatePhraseClient，无网络）。
LLMPhraseClient 为预留桩（默认禁用），未来接入时只负责「重述 content 文案」，绝不改 tier/score。

Protocol 化的好处：strategy/opinion 引擎只依赖 PhraseClient 接口，便于后续替换实现。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PhraseClient(Protocol):
    def phrase(self, text: str) -> str:
        """对模板文本做最终润色（可重述，不可改变数值/档位）。"""
        ...


class TemplatePhraseClient:
    """确定性模板客户端：原样返回（identity）。无网络、可复现，便于自测。"""

    def phrase(self, text: str) -> str:
        return text


class LLMPhraseClient:
    """预留 LLM 润色客户端（P3 不启用）。

    未来接入时：仅调用 LLM 重述 `text` 的中文表达，返回文案；
    绝不得修改 signal_type / score / confidence / position 等任何数值。
    当前直接抛 NotImplementedError，确保「未显式启用绝不悄悄走网络」。
    """

    def __init__(self, *, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key
        self.model = model

    def phrase(self, text: str) -> str:
        raise NotImplementedError(
            "LLMPhraseClient 在 P3 未启用（D1）。接入时只润色文案，不判断。"
        )
