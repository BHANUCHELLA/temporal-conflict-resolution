#!/usr/bin/env python3
"""
Interactive demo — runs all edge cases with formatted output.

    python demo.py
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.models import TransactionEvent
from src.database import Database
from src.reconciliation_engine import ReconciliationEngine

BOLD = "\033[1m"
DIM  = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"


def banner(text):
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  {text}{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}\n")


def show_result(result):
    nc = len(result.conflicts_detected)
    nw = len(result.warnings)

    if nw:
        badge = f"{YELLOW}WARNED{RESET}"
    elif nc:
        badge = f"{CYAN}RESOLVED ({nc} conflict{'s' if nc != 1 else ''}){RESET}"
    else:
        badge = f"{GREEN}OK{RESET}"

    txn = result.transaction_id
    print(f"  {BOLD}{txn}{RESET}  [{badge}]")

    if result.final_state:
        s = result.final_state
        print(f"    v{s.version}  src={s.source}  amt={s.amount}  "
              f"cur={s.currency}  status={s.status}")

    for w in result.warnings:
        print(f"    {YELLOW}!{RESET} {w}")
    for c in result.conflicts_detected:
        print(f"    {DIM}conflict{RESET} [{c['field']}] "
              f"{c['existing_source']}({c['existing_value']}) "
              f"vs {c['incoming_source']}({c['incoming_value']})")
    for r in result.resolutions_applied:
        print(f"    {GREEN}->{RESET} [{r['field']}] = {r['resolved_value']}  "
              f"({r['reason']})")
    print()


def run_demo():
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    db = Database(":memory:", schema_path=schema_path)
    engine = ReconciliationEngine(db)

    # ── Scenario 1: Duplicate Events ─────────────────────────────
    banner("SCENARIO 1 — Duplicate Events")
    for _ in range(3):
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-DEMO-001", source="PrimaryBank",
            timestamp="2024-03-15T10:30:00Z", amount=1500.0,
            currency="USD", status="Pending", confidence_score=0.95,
        ))
    show_result(r)
    print(f"    {DIM}(3 events sent; only 1 processed, 2 detected as duplicates){RESET}\n")

    # ── Scenario 2: Conflicting Amounts ──────────────────────────
    banner("SCENARIO 2 — Conflicting Amounts (PrimaryBank wins)")
    engine.process_event(TransactionEvent(
        transaction_id="TXN-DEMO-002", source="SecondaryBank",
        timestamp="2024-03-15T10:30:00Z", amount=2500.0,
        currency="USD", status="Pending", confidence_score=0.80,
    ))
    r = engine.process_event(TransactionEvent(
        transaction_id="TXN-DEMO-002", source="PrimaryBank",
        timestamp="2024-03-15T10:31:00Z", amount=2750.0,
        currency="USD", status="Pending", confidence_score=0.95,
    ))
    show_result(r)

    # ── Scenario 3: Late Event ───────────────────────────────────
    banner("SCENARIO 3 — Late Event (earlier timestamp, arrived last)")
    engine.process_event(TransactionEvent(
        transaction_id="TXN-DEMO-003", source="SecondaryBank",
        timestamp="2024-03-15T10:30:00Z", amount=500.0,
        currency="GBP", status="Completed", confidence_score=0.75,
    ))
    r = engine.process_event(TransactionEvent(
        transaction_id="TXN-DEMO-003", source="PrimaryBank",
        timestamp="2024-03-15T09:00:00Z", amount=525.0,
        currency="GBP", status="Pending", confidence_score=0.95,
    ))
    show_result(r)

    # ── Scenario 4: Missing Fields ───────────────────────────────
    banner("SCENARIO 4 — Missing Source & Timestamp")
    r = engine.process_event(TransactionEvent(
        transaction_id="TXN-DEMO-004", amount=800.0, currency="USD",
    ))
    show_result(r)

    # ── Scenario 5: Invalid Status Transition ────────────────────
    banner("SCENARIO 5 — Invalid Status Transition (Pending → Completed, skips Approved)")
    engine.process_event(TransactionEvent(
        transaction_id="TXN-DEMO-005", source="PrimaryBank",
        timestamp="2024-03-15T10:00:00Z", amount=3000.0, status="Pending",
        confidence_score=0.9,
    ))
    r = engine.process_event(TransactionEvent(
        transaction_id="TXN-DEMO-005", source="PrimaryBank",
        timestamp="2024-03-15T10:05:00Z", amount=3000.0, status="Completed",
        confidence_score=0.9,
    ))
    show_result(r)
    print(f"    {DIM}(same source wins the field conflict, but the status jump itself"
          f" is invalid, so it's rejected and 'Pending' is retained){RESET}\n")

    # ── Scenario 6: Midnight Crossing ────────────────────────────
    banner("SCENARIO 6 — Midnight Boundary Crossing")
    engine.process_event(TransactionEvent(
        transaction_id="TXN-DEMO-006", source="PrimaryBank",
        timestamp="2024-03-15T23:59:59Z", amount=1000.0,
        currency="USD", status="Pending",
        confidence_score=0.90, event_sequence=1,
    ))
    r = engine.process_event(TransactionEvent(
        transaction_id="TXN-DEMO-006", source="SecondaryBank",
        timestamp="2024-03-16T00:00:01Z", amount=1050.0,
        currency="USD", status="Approved",
        confidence_score=0.85, event_sequence=2,
    ))
    show_result(r)

    # ── Final Report ─────────────────────────────────────────────
    banner("FINAL RECONCILIATION REPORT")
    report = engine.generate_final_report()
    for entry in report:
        txn = entry["transaction_id"]
        fs = entry["final_state"]
        print(f"  {BOLD}{txn}{RESET}  v{fs['version']}  "
              f"src={fs['source']}  amt={fs['amount']}  "
              f"cur={fs['currency']}  status={fs['status']}")
        print(f"    events={entry['events_considered']}  "
              f"conflicts={entry['conflicts_detected']}  "
              f"audit_entries={len(entry['audit_trail'])}")
    print()

    db.close()
    print(f"{GREEN}  Demo complete.{RESET}\n")


if __name__ == "__main__":
    run_demo()
    