"""Start a portable copy of Deck."""

import sys
from pathlib import Path

from app import main


if __name__ == "__main__":
    app = Path(__file__).resolve().with_name("app.py")
    sys.argv = [str(app), "--open", *sys.argv[1:]]
    main()
