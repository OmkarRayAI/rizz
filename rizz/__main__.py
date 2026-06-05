"""Allow `python -m rizz ...` even without `pip install`."""
from .cli import main

if __name__ == "__main__":
    main()
