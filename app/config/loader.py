from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass
class AppConfig:
    timezone: str
    daily_limit: int
    delay_between_uids_sec: int
    page_load_countdown_sec: int
    retry_max_attempts: int
    retry_backoff_sec: int
    result_decrement_on: str
    capture_screenshots_on_fail: bool
    db_path: Path
    evidence_dir: Path

    @classmethod
    def from_mapping(cls, data: Dict[str, Any]) -> "AppConfig":
        return cls(
            timezone=data.get("timezone", "UTC"),
            daily_limit=int(data.get("daily_limit", 10)),
            delay_between_uids_sec=int(data.get("delay_between_uids_sec", 10)),
            page_load_countdown_sec=int(data.get("page_load_countdown_sec", 10)),
            retry_max_attempts=int(data.get("retry_max_attempts", 3)),
            retry_backoff_sec=int(data.get("retry_backoff_sec", 10)),
            result_decrement_on=str(data.get("result_decrement_on", "terminal")),
            capture_screenshots_on_fail=bool(data.get("capture_screenshots_on_fail", False)),
            db_path=Path(data.get("db_path", "data/app.db")).expanduser(),
            evidence_dir=Path(data.get("evidence_dir", "data/evidence")).expanduser(),
        )


def load_config(config_path: str | os.PathLike[str] | None = None) -> AppConfig:
    base_dir = Path(__file__).resolve().parent
    default_path = base_dir / "defaults.json"
    path = Path(config_path) if config_path else default_path

    if not path.exists():
        if config_path:
            raise FileNotFoundError(f"Config file not found: {path}")
        return AppConfig.from_mapping({})

    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    config = AppConfig.from_mapping(data)
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    return config


__all__ = ["AppConfig", "load_config"]
