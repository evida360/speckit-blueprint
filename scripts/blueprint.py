#!/usr/bin/env python3
"""speckit-blueprint command line. Run `blueprint.py --help`; the code lives in speckit_blueprint/."""

import sys
from pathlib import Path

# The extension is installed inside the user's project (.specify/extensions/blueprint/): never leave
# __pycache__ folders behind in it.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from speckit_blueprint.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
