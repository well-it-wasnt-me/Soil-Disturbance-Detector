"""Convenience launcher for the CLI without installation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dgd.cli import parse_args
from dgd.pipeline import DisturbanceDetector, NullProgress, TqdmProgress


def main():
    args = parse_args()
    progress = NullProgress() if args.no_progress else TqdmProgress(total_steps=10)
    DisturbanceDetector(args, progress=progress).run()


if __name__ == "__main__":
    main()
