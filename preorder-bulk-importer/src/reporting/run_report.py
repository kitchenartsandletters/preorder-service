from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..models import Anomaly


@dataclass
class RunContext:
    run_id: str


@dataclass
class RunReport:
    run_id: str
    input_source: str
    template: Optional[str] = None
    rows_total: int = 0
    rows_ok: int = 0
    rows_failed: int = 0
    failures: List[Anomaly] = field(default_factory=list)

    def to_json(self, path: Path) -> None:
        payload = asdict(self)
        # dataclasses inside list
        payload["failures"] = [asdict(a) for a in self.failures]
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def new_run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")