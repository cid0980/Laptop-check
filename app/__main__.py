"""Allow `python -m app` as an alias for `python run.py`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
