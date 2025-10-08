from __future__ import annotations

import itertools
from pathlib import Path
from typing import Iterator, List


class MessageProvider:
    def __init__(self, messages_file: Path) -> None:
        self._messages_file = messages_file
        self._messages: List[str] = []
        self._cycle: Iterator[str] | None = None
        self.reload()

    def reload(self) -> None:
        if not self._messages_file.exists():
            raise FileNotFoundError(f"messages file not found: {self._messages_file}")
        with self._messages_file.open("r", encoding="utf-8") as fh:
            messages = [line.strip() for line in fh if line.strip()]
        if not messages:
            raise ValueError("messages file is empty")
        self._messages = messages
        self._cycle = itertools.cycle(self._messages)

    def next_message(self) -> str:
        if not self._cycle:
            self.reload()
        assert self._cycle is not None
        return next(self._cycle)


__all__ = ["MessageProvider"]
