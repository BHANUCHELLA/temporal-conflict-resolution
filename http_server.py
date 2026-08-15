#!/usr/bin/env python3
"""
Bonus scope: real-time HTTP endpoint to receive transaction events.

Stdlib-only (http.server), so it respects the PRD constraint of
"no external APIs, Kafka, or distributed systems" -- this is a single-process
local server, not a distributed system, and adds zero third-party dependencies.

Deliberately single-threaded (plain HTTPServer, not ThreadingHTTPServer):
sqlite3 connections are tied to the thread that created them, and the engine
must see a consistent, serialized view of state to stay deterministic anyway.
For a local demo/bonus feature this is the correct trade-off over adding
locking complexity.

    python http_server.py --port 8080

Endpoints:
    POST /events        body: single event JSON, or a JSON array of events
                         -> per-event ReconciliationResult(s)
    GET  /transactions   -> full reconciliation report (same shape as --report)
    GET  /transactions/<id> -> final state + audit trail for one transaction
    GET  /health         -> {"status": "ok"}

Example:
    curl -X POST localhost:8080/events -d '{
      "transaction_id": "TXN-HTTP-001", "source": "PrimaryBank",
      "timestamp": "2024-03-15T10:00:00Z", "amount": 100.0,
      "currency": "USD", "status": "Pending"
    }'
    curl localhost:8080/transactions
"""

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.models import TransactionEvent
from src.database import Database
from src.reconciliation_engine import ReconciliationEngine

KNOWN_FIELDS = {
    "transaction_id", "source", "timestamp", "amount", "currency",
    "status", "reconciliation_notes", "confidence_score", "event_sequence",
}


def make_handler(engine: ReconciliationEngine, db: Database):
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, payload, status=200):
            body = json.dumps(payload, indent=2, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            sys.stderr.write("  " + (fmt % args) + "\n")

        def do_GET(self):
            if self.path == "/health":
                return self._send_json({"status": "ok"})

            if self.path == "/transactions":
                return self._send_json(engine.generate_final_report())

            m = re.match(r"^/transactions/([^/]+)$", self.path)
            if m:
                txn_id = m.group(1)
                state = db.get_state(txn_id)
                if state is None:
                    return self._send_json({"error": f"unknown transaction_id '{txn_id}'"}, status=404)
                return self._send_json({
                    "transaction_id": txn_id,
                    "final_state": state.to_dict(),
                    "audit_trail": db.get_audit_trail(txn_id),
                    "conflicts": db.get_conflicts(txn_id),
                })

            self._send_json({"error": "not found"}, status=404)

        def do_POST(self):
            if self.path != "/events":
                return self._send_json({"error": "not found"}, status=404)

            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                return self._send_json({"error": f"invalid JSON body: {e}"}, status=400)

            payloads = data if isinstance(data, list) else [data]
            results = []
            for p in payloads:
                if "transaction_id" not in p:
                    results.append({"error": "missing required field 'transaction_id'", "input": p})
                    continue
                filtered = {k: v for k, v in p.items() if k in KNOWN_FIELDS}
                event = TransactionEvent(**filtered)
                result = engine.process_event(event)
                results.append(result.to_dict())

            self._send_json(results if len(results) > 1 else results[0], status=201)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Real-time reconciliation HTTP endpoint (bonus scope).")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", default=":memory:")
    parser.add_argument("--schema", default=None)
    args = parser.parse_args()

    db = Database(args.db, schema_path=args.schema)
    engine = ReconciliationEngine(db)

    server = HTTPServer(("127.0.0.1", args.port), make_handler(engine, db))
    print(f"Reconciliation HTTP endpoint listening on http://127.0.0.1:{args.port}")
    print("  POST /events              -- submit one event or a JSON array of events")
    print("  GET  /transactions        -- full reconciliation report")
    print("  GET  /transactions/<id>   -- final state + audit trail for one transaction")
    print("  GET  /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
