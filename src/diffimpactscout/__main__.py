"""Module entry point: ``python -m diffimpactscout`` runs the CLI."""

import sys

from diffimpactscout.cli import main

if __name__ == "__main__":
    sys.exit(main())