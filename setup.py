#!/usr/bin/env python3
"""
Optional sanity-check script.

Verifies that all required project files/directories are present and
that the core package imports cleanly. This does NOT generate any files --
everything in this repo is committed source, not generated at setup time.

    python setup.py
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

REQUIRED_PATHS = [
    "src/models.py",
    "src/database.py",
    "src/reconciliation_engine.py",
    "src/cli.py",
    "schema.sql",
    "tests",
    "sample_inputs",
    "notebooks/visualization.ipynb",
]


def main():
    missing = [p for p in REQUIRED_PATHS if not os.path.exists(os.path.join(BASE, p))]
    if missing:
        print("Missing required files/directories:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    sys.path.insert(0, BASE)
    try:
        from src.models import TransactionEvent  # noqa: F401
        from src.database import Database  # noqa: F401
        from src.reconciliation_engine import ReconciliationEngine  # noqa: F401
    except ImportError as e:
        print(f"Core package failed to import: {e}")
        sys.exit(1)

    print("All required files are present and the core package imports cleanly.")
    print("Next steps:")
    print("  python demo.py                 # interactive walkthrough of 6 edge cases")
    print("  python main.py --test          # run the full test suite")
    print("  python main.py --benchmark     # 1200-event performance benchmark")


if __name__ == "__main__":
    main()
