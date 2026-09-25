"""会话状态机（docs/tech-design.md §2.6，G18）。

状态集合见 :data:`memex.constants.SESSION_STATES`。合法迁移是单向推进：
begun → evidence_taken → drafting → validated → committed，
任意非终态都可在 TTL 到期时被回收为 abandoned。终态（committed / abandoned）不再迁移。
"""

from __future__ import annotations

from ..constants import SESSION_STATES

TERMINAL_STATES: tuple[str, ...] = ("committed", "abandoned")

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "begun": frozenset({"evidence_taken", "drafting", "abandoned"}),
    "evidence_taken": frozenset({"drafting", "abandoned"}),
    "drafting": frozenset({"validated", "abandoned"}),
    "validated": frozenset({"committed", "drafting", "abandoned"}),
    "committed": frozenset(),
    "abandoned": frozenset(),
}


def is_state(value: str) -> bool:
    return value in SESSION_STATES


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def can_transition(old: str, new: str) -> bool:
    """旧 -> 新 是否合法（允许原地不动）。"""
    if old == new:
        return True
    return new in ALLOWED_TRANSITIONS.get(old, frozenset())


def next_state_after_begin(include_pack: bool) -> str:
    """开会话后的初始状态：附带证据包则直接进入 evidence_taken。"""
    return "evidence_taken" if include_pack else "begun"
