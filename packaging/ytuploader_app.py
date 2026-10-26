"""PyInstaller entry script for ytuploader.exe (the desktop app)."""

import sys

from ytuploader.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
