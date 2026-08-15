"""
Command-line interface.

    python main.py --input sample_inputs/comprehensive_all_cases.json
    python main.py --input events.json --verbose
    python main.py --input events.json --report --output audit_output/report.json
    cat events.json | python main.py
"""

import argparse
import json
import os
import sys

from .models import TransactionEvent
from .database import Database
from .reconciliation_engine import ReconciliationEngine


class InputValidationError(Exception):
    """Raised for a structurally invalid event, with a message safe to print to the user."""


def load_events(path):
    if path:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise InputValidationError(
            f"input must be a JSON object or array of objects, got {type(data).__name__}"
        )

    known = {
        "transaction_id", "source", "timestamp", "amount", "currency",
        "status", "reconciliation_notes", "confidence_score", "event_sequence",
    }
    numeric_fields = {"amount", "confidence_score", "event_sequence"}

    events = []
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise InputValidationError(
                f"event at index {i} must be a JSON object, got {type(raw).__name__}: {raw!r}"
            )
        if "transaction_id" not in raw or raw["transaction_id"] in (None, ""):
            raise InputValidationError(f"event at index {i} is missing required field 'transaction_id': {raw!r}")

        filtered = {k: v for k, v in raw.items() if k in known}

        for field in numeric_fields:
            value = filtered.get(field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InputValidationError(
                    f"event at index {i} (transaction_id={raw['transaction_id']!r}): "
                    f"field '{field}' must be a number, got {type(value).__name__} ({value!r})"
                )

        events.append(TransactionEvent(**filtered))
    return events


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Temporal Conflict Resolution — deterministic reconciliation engine",
    )
    parser.add_argument("--input", "-i", help="Path to a JSON file of events (default: stdin)")
    parser.add_argument("--db", default=":memory:", help="SQLite DB path (default: in-memory)")
    parser.add_argument("--schema", default=None, help="Path to schema.sql (default: ./schema.sql)")
    parser.add_argument("--output", "-o", help="Path to write the JSON report (with --report)")
    parser.add_argument("--report", action="store_true", help="Write the full reconciliation report as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print per-event JSON results as they're processed")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        events = load_events(args.input)
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)
    except InputValidationError as e:
        print(f"error: invalid event data: {e}", file=sys.stderr)
        sys.exit(1)

    if not events:
        print("No events to process.")
        return

    db = Database(args.db, schema_path=args.schema)
    engine = ReconciliationEngine(db)
    results = engine.process_events(events)

    if args.verbose:
        for r in results:
            print(json.dumps(r.to_dict(), indent=2, default=str))

    report = engine.generate_final_report()

    if args.report:
        out_path = args.output or "audit_output/report.json"
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Report written to {out_path}  ({len(report)} transactions)")
    else:
        print(f"\nProcessed {len(events)} events across {len(report)} transactions.\n")
        for entry in report:
            fs = entry["final_state"]
            source = fs["source"] or "-"
            amount = fs["amount"] if fs["amount"] is not None else "-"
            currency = fs["currency"] or "-"
            status = fs["status"] or "-"
            print(
                f"  {entry['transaction_id']:<16} v{fs['version']:<3} "
                f"source={source:<14} amount={amount!s:<10} "
                f"currency={currency:<5} status={status:<10} "
                f"events={entry['events_considered']} conflicts={entry['conflicts_detected']}"
            )
        print()

    db.close()


if __name__ == "__main__":
    main()
