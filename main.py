"""CLI entrypoint for disturbed ground detection.

This thin wrapper parses arguments and executes the pipeline implemented in
``dgd.pipeline.DisturbanceDetector``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure `src/` is importable when running from the repo root without installing.
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dgd.cli import parse_args
from dgd.pipeline import DisturbanceDetector, NullProgress, TqdmProgress


def main() -> None:
    """Parse CLI args and run the detection pipeline."""

    args = parse_args()
    progress = NullProgress() if args.no_progress else TqdmProgress(total_steps=10)
    detector = DisturbanceDetector(args, progress=progress)
    detector.run()


if __name__ == "__main__":
    main()
