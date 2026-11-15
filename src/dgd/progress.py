"""Progress reporting. A bar that moves is the only proof most people accept that work is happening."""

from __future__ import annotations

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


__all__ = ["NullProgress", "TqdmProgress"]
