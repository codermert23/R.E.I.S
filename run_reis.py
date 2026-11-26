"""PyInstaller giriş noktası (python -m reis ile aynı)."""
import sys

from reis.app import main

if __name__ == "__main__":
    sys.exit(main())
