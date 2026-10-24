"""Keep user data beside the installed executable, outside bundled resources."""
import sys
from pathlib import Path

VERSION = "1.0.0"


def data_dir():
    if getattr(sys, "frozen", False):
        directory = Path(sys.executable).resolve().parent / "data"
    else:
        directory = Path(__file__).resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    return directory
