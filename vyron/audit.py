"""A visible audit trail: what the assistant did and why, plus a running model-cost tally."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import STATE_DIR


class Audit:
    def __init__(self, path: Path, input_usd_per_mtok: float = 0.0, output_usd_per_mtok: float = 0.0):
        self.path = path
        self.in_rate = input_usd_per_mtok / 1_000_000
        self.out_rate = output_usd_per_mtok / 1_000_000
        self.session_usd = 0.0
        self.session_tokens = {"input": 0, "output": 0}
        self._lock = threading.Lock()

    def log(self, kind: str, **data: Any) -> None:
        entry = {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, **data}
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def model_usage(self, input_tokens: int, output_tokens: int, source: str = "chat") -> float:
        cost = input_tokens * self.in_rate + output_tokens * self.out_rate
        with self._lock:
            self.session_usd += cost
            self.session_tokens["input"] += input_tokens
            self.session_tokens["output"] += output_tokens
        self.log("model_call", source=source, input_tokens=input_tokens, output_tokens=output_tokens,
                 usd=round(cost, 6), session_usd=round(self.session_usd, 4))
        return cost

    def tail(self, n: int = 20) -> list[str]:
        if not self.path.exists():
            return []
        return self.path.read_text(encoding="utf-8").splitlines()[-n:]


def default_audit(config) -> Audit:
    m = config["model"]
    free = m.get("provider") != "anthropic"   # a local model costs nothing per token
    return Audit(
        config.path("audit", "path", STATE_DIR / "audit.log"),
        0.0 if free else float(m.get("input_usd_per_mtok", 0)),
        0.0 if free else float(m.get("output_usd_per_mtok", 0)),
    )
