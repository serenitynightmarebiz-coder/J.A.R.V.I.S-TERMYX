#!/usr/bin/env python3
"""J.A.R.V.I.S. entry point.

    python jarvis.py                interactive session
    python jarvis.py --setup        first-run wizard
    python jarvis.py --voice        hands-free wake-word mode
    python jarvis.py "open spotify" one-shot
    python jarvis.py --selftest     offline test of every subsystem
"""

import os
import sys

# Keep the import root stable when launched from anywhere (Termux widgets, cron).
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from jarvis.cli import main  # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  · powering down")
        sys.exit(130)
