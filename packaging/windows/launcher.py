"""What DGD.exe runs. Kept outside the package so ``dgd.app`` is imported once, as a module."""

import sys

from dgd.app import main

if __name__ == "__main__":
    sys.exit(main())
