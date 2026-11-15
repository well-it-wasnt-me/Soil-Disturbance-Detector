"""Run the detector from a checkout without installing it.

``python main.py scan --lat ... --lon ...`` is the same as ``dgd scan ...``
after ``pip install -e .``. Old-style calls with no subcommand still go to
``classify``, for anyone with 0.1 scripts lying around.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make src/ importable when running from the repo root without installing.
SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dgd.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
