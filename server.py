"""FrontierAgent Web Server Root Entrypoint."""

import os
import sys
from pathlib import Path

# Ensure root FrontierAgent directory is in sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from apodex.web_server import main

if __name__ == "__main__":
    main()
