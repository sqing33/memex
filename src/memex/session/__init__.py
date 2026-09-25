"""会话层（G18）：状态机 + TTL 回收 + 统计归档。"""

from __future__ import annotations

from .manager import (
    archive_stats,
    begin_session,
    find_active_session,
    finish,
    get_session,
    load_session,
    new_session_id,
    session_stats_summary,
    set_state,
    sweep,
    touch,
)
from .state import ALLOWED_TRANSITIONS, TERMINAL_STATES, can_transition, is_terminal, next_state_after_begin

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "archive_stats",
    "begin_session",
    "can_transition",
    "find_active_session",
    "finish",
    "get_session",
    "is_terminal",
    "load_session",
    "new_session_id",
    "next_state_after_begin",
    "session_stats_summary",
    "set_state",
    "sweep",
    "touch",
]
