from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunState:
    is_running: bool = False
    stop_requested: bool = False
    pending_save_count: int = 0
    last_failed_indices: set[int] = field(default_factory=set)
