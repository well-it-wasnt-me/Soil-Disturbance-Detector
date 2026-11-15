"""Progress reporting. A bar that moves is the only proof most people accept that work is happening."""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import Callable


class NullProgress:
    """Progress that says nothing. For scripts, tests and the strong silent type."""

    def step(self, message: str) -> None:
        pass

    def counter(self, total: int, desc: str) -> tuple[Callable[[], None], Callable[[], None]]:
        return (lambda: None), (lambda: None)

    def close(self) -> None:
        pass


class TqdmProgress:
    """tqdm bar for pipeline milestones, plus nested counters for long loops."""

    def __init__(self, total_steps: int, label: str = "Pipeline"):
        from tqdm.auto import tqdm

        self._tqdm = tqdm
        self.bar = tqdm(total=total_steps, desc=label, leave=False)

    def step(self, message: str) -> None:
        self.bar.write(message)
        self.bar.update(1)

    def counter(self, total: int, desc: str) -> tuple[Callable[[], None], Callable[[], None]]:
        sub = self._tqdm(total=total, desc=desc, leave=False, position=1)
        return (lambda: sub.update(1)), sub.close

    def close(self) -> None:
        self.bar.close()


class JsonProgress:
    """Progress as one JSON line per change on stdout, for the map UI to read.

    Every line starts with ``PREFIX``, so the UI can tell progress from log
    output in the same pipe. Counters report at most twice a second, plus
    the final count, which is plenty for a bar and spares the pipe.
    """

    PREFIX = "@@dgd-progress "

    def __init__(self, total_steps: int, stream=None, min_interval: float = 0.5):
        self.total = total_steps
        self.stream = stream if stream is not None else sys.stdout
        self.min_interval = min_interval
        self.n = 0
        self.label = ""
        self._lock = threading.Lock()

    def _emit(self, **data) -> None:
        with self._lock:
            self.stream.write(self.PREFIX + json.dumps(data) + "\n")
            self.stream.flush()

    def step(self, message: str) -> None:
        self.n += 1
        self.label = message
        self._emit(step=self.n, total=self.total, label=message)

    def counter(self, total: int, desc: str) -> tuple[Callable[[], None], Callable[[], None]]:
        state = {"done": 0, "last": float("-inf")}

        def tick() -> None:
            state["done"] += 1
            now = time.monotonic()
            if state["done"] >= total or now - state["last"] >= self.min_interval:
                state["last"] = now
                self._emit(step=self.n, total=self.total, label=self.label, done=state["done"], of=total, unit=desc)

        return tick, (lambda: None)

    def close(self) -> None:
        pass


def parse_progress(line: str) -> dict | None:
    """The progress dict in a ``JsonProgress`` line, or None for any other line."""
    if not line.startswith(JsonProgress.PREFIX):
        return None
    try:
        data = json.loads(line[len(JsonProgress.PREFIX):])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


__all__ = ["JsonProgress", "NullProgress", "TqdmProgress", "parse_progress"]
