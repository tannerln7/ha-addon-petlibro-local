from pathlib import Path
import sys


APP_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "petlibro-local"
    / "appdaemon"
    / "src"
)
sys.path.insert(0, str(APP_SOURCE))
