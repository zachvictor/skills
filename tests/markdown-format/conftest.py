"""Put markdown-format/scripts on sys.path so tests can import the script."""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "markdown-format" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
