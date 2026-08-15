#!/usr/bin/env python3
"""
Entry point.

    python main.py --input events.json
    python main.py --test
    python main.py --benchmark
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_tests():
    import unittest
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def run_benchmark():
    import time
    import random
    from src.models import TransactionEvent
    from src.database import Database
    from src.reconciliation_engine import ReconciliationEngine

    N = 1200
    TXNS = 200
    sources = ["PrimaryBank", "SecondaryBank", "TertiaryBank", "PartnerX"]
    statuses = ["Pending", "Approved", "Completed", "Rejected"]
    currencies = ["USD", "EUR", "GBP", "JPY"]

    events = [
        TransactionEvent(
            transaction_id=f"BENCH-{random.randint(1, TXNS):04d}",
            source=random.choice(sources),
            timestamp=f"2024-03-15T{random.randint(0,23):02d}:"
                      f"{random.randint(0,59):02d}:{random.randint(0,59):02d}Z",
            amount=round(random.uniform(1, 50000), 2),
            currency=random.choice(currencies),
            status=random.choice(statuses),
            confidence_score=round(random.uniform(0.5, 1.0), 2),
            event_sequence=random.randint(1, 100),
        )
        for _ in range(N)
    ]

    db = Database(":memory:")
    engine = ReconciliationEngine(db)

    print(f"Processing {N} events across {TXNS} transactions...")
    t0 = time.perf_counter()
    engine.process_events(events)
    elapsed = time.perf_counter() - t0

    n_states = len(db.get_all_states())
    n_conf = db.conn.execute("SELECT COUNT(*) FROM conflict_log").fetchone()[0]
    n_audit = db.conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]

    print(f"\n  Events:       {N}")
    print(f"  Transactions: {n_states}")
    print(f"  Conflicts:    {n_conf}")
    print(f"  Audit entries:{n_audit}")
    print(f"  Time:         {elapsed:.3f}s")
    print(f"  Throughput:   {N / elapsed:.0f} events/sec")
    print(f"\n  {'PASS' if elapsed < 5.0 else 'FAIL'} — "
          f"{'under' if elapsed < 5.0 else 'EXCEEDED'} 5s threshold\n")

    db.close()


def main():
    if "--test" in sys.argv:
        sys.argv = [sys.argv[0]]
        run_tests()
    elif "--benchmark" in sys.argv:
        run_benchmark()
    else:
        from src.cli import main as cli_main
        cli_main()


if __name__ == "__main__":
    main()
    