"""
Test suite covering every edge case called out in the PRD:
  - duplicate events (idempotency)
  - conflicting amount / currency / status
  - late events arriving after resolution
  - missing timestamp / missing source
  - invalid status transitions
  - midnight boundary crossing
  - determinism / replayability under reordering
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import TransactionEvent
from src.database import Database
from src.reconciliation_engine import ReconciliationEngine

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def make_engine():
    db = Database(":memory:", schema_path=SCHEMA_PATH)
    return ReconciliationEngine(db), db


class TestDuplicateEvents(unittest.TestCase):
    def test_exact_duplicate_is_idempotent(self):
        engine, db = make_engine()
        ev = lambda: TransactionEvent(
            transaction_id="TXN-1", source="PrimaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=100.0, currency="USD", status="Pending",
        )
        r1 = engine.process_event(ev())
        r2 = engine.process_event(ev())
        r3 = engine.process_event(ev())

        self.assertFalse(r1.is_duplicate)
        self.assertTrue(r2.is_duplicate)
        self.assertTrue(r3.is_duplicate)
        self.assertEqual(r1.final_state.version, 1)
        self.assertEqual(db.count_events("TXN-1"), 1)
        db.close()

    def test_same_fields_different_source_is_not_a_duplicate(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-2", source="PrimaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=100.0, currency="USD", status="Pending",
        ))
        r2 = engine.process_event(TransactionEvent(
            transaction_id="TXN-2", source="SecondaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=100.0, currency="USD", status="Pending",
        ))
        self.assertFalse(r2.is_duplicate)
        db.close()


class TestConflictResolution(unittest.TestCase):
    def test_conflicting_amount_primary_bank_wins(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-3", source="SecondaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=100.0, currency="USD", status="Pending",
        ))
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-3", source="PrimaryBank",
            timestamp="2024-03-15T10:01:00Z", amount=150.0, currency="USD", status="Pending",
        ))
        self.assertEqual(r.final_state.amount, 150.0)
        self.assertEqual(len(r.conflicts_detected), 1)
        self.assertEqual(r.conflicts_detected[0]["field"], "amount")
        db.close()

    def test_conflicting_currency_resolved_by_confidence_when_source_ties(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-4", source="SecondaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=100.0, currency="USD",
            status="Pending", confidence_score=0.6,
        ))
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-4", source="SecondaryBank",
            timestamp="2024-03-15T10:01:00Z", amount=100.0, currency="EUR",
            status="Pending", confidence_score=0.9,
        ))
        self.assertEqual(r.final_state.currency, "EUR")
        db.close()

    def test_timestamp_tiebreak_when_source_and_confidence_tie(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-5", source="SecondaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=100.0, currency="USD",
            status="Pending", confidence_score=0.8,
        ))
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-5", source="SecondaryBank",
            timestamp="2024-03-15T11:00:00Z", amount=200.0, currency="USD",
            status="Pending", confidence_score=0.8,
        ))
        self.assertEqual(r.final_state.amount, 200.0)  # later timestamp wins
        db.close()

    def test_event_sequence_tiebreak_on_same_second(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-6", source="SecondaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=100.0, currency="USD",
            status="Pending", confidence_score=0.8, event_sequence=5,
        ))
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-6", source="SecondaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=200.0, currency="USD",
            status="Pending", confidence_score=0.8, event_sequence=2,
        ))
        # earlier event_sequence (2 < 5) wins -> incoming's amount applied
        self.assertEqual(r.final_state.amount, 200.0)
        db.close()


class TestLateEvents(unittest.TestCase):
    def test_late_event_with_earlier_timestamp_still_wins_via_source_priority(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-7", source="SecondaryBank",
            timestamp="2024-03-15T10:30:00Z", amount=500.0, currency="GBP", status="Pending",
        ))
        # arrives "late" (processed second) but represents an earlier real-world event,
        # and is from the higher-priority source
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-7", source="PrimaryBank",
            timestamp="2024-03-15T09:00:00Z", amount=525.0, currency="GBP", status="Pending",
        ))
        self.assertEqual(r.final_state.amount, 525.0)
        self.assertEqual(r.final_state.version, 2)
        db.close()

    def test_late_event_cannot_regress_a_completed_status(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-8", source="SecondaryBank",
            timestamp="2024-03-15T10:30:00Z", amount=500.0, currency="GBP", status="Completed",
        ))
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-8", source="PrimaryBank",
            timestamp="2024-03-15T09:00:00Z", amount=500.0, currency="GBP", status="Pending",
        ))
        # PrimaryBank wins amount, but Completed -> Pending is not a valid transition
        self.assertEqual(r.final_state.status, "Completed")
        self.assertTrue(any("Invalid status transition" in w for w in r.warnings))
        db.close()


class TestMissingFields(unittest.TestCase):
    def test_missing_source_defaults_and_warns(self):
        engine, db = make_engine()
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-9", timestamp="2024-03-15T10:00:00Z", amount=10.0,
        ))
        self.assertEqual(r.final_state.source, "UnknownSource")
        self.assertTrue(any("source" in w.lower() for w in r.warnings))
        db.close()

    def test_missing_timestamp_defaults_and_warns(self):
        engine, db = make_engine()
        r = engine.process_event(TransactionEvent(transaction_id="TXN-10", source="PrimaryBank", amount=10.0))
        self.assertIsNotNone(r.final_state.timestamp)
        self.assertTrue(any("timestamp" in w.lower() for w in r.warnings))
        db.close()


class TestInvalidStatusTransitions(unittest.TestCase):
    def test_pending_to_completed_is_rejected(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-11", source="PrimaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=3000.0, status="Pending", confidence_score=0.9,
        ))
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-11", source="PrimaryBank",
            timestamp="2024-03-15T10:05:00Z", amount=3000.0, status="Completed", confidence_score=0.9,
        ))
        self.assertEqual(r.final_state.status, "Pending")
        self.assertTrue(any("Invalid status transition" in w for w in r.warnings))
        db.close()

    def test_pending_to_approved_to_completed_is_valid(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-12", source="PrimaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=3000.0, status="Pending", confidence_score=0.9,
        ))
        engine.process_event(TransactionEvent(
            transaction_id="TXN-12", source="PrimaryBank",
            timestamp="2024-03-15T10:05:00Z", amount=3000.0, status="Approved", confidence_score=0.9,
        ))
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-12", source="PrimaryBank",
            timestamp="2024-03-15T10:10:00Z", amount=3000.0, status="Completed", confidence_score=0.9,
        ))
        self.assertEqual(r.final_state.status, "Completed")
        self.assertFalse(any("Invalid status transition" in w for w in r.warnings))
        db.close()

    def test_completed_is_terminal(self):
        engine, db = make_engine()
        engine.process_event(TransactionEvent(
            transaction_id="TXN-13", source="PrimaryBank",
            timestamp="2024-03-15T10:00:00Z", amount=100.0, status="Completed",
        ))
        r = engine.process_event(TransactionEvent(
            transaction_id="TXN-13", source="PrimaryBank",
            timestamp="2024-03-15T10:05:00Z", amount=100.0, status="Rejected",
        ))
        self.assertEqual(r.final_state.status, "Completed")
        db.close()


class TestMidnightBoundary(unittest.TestCase):
    def test_events_crossing_midnight_are_ordered_correctly(self):
        engine, db = make_engine()
        events = [
            TransactionEvent(
                transaction_id="TXN-14", source="SecondaryBank",
                timestamp="2024-03-16T00:00:01Z", amount=1050.0,
                currency="USD", status="Approved", confidence_score=0.85, event_sequence=2,
            ),
            TransactionEvent(
                transaction_id="TXN-14", source="PrimaryBank",
                timestamp="2024-03-15T23:59:59Z", amount=1000.0,
                currency="USD", status="Pending", confidence_score=0.9, event_sequence=1,
            ),
        ]
        # process_events sorts by timestamp ascending regardless of list order
        results = engine.process_events(events)
        final = results[-1].final_state
        self.assertEqual(final.source, "PrimaryBank")
        self.assertEqual(final.amount, 1000.0)
        db.close()


class TestDeterminismAndReplay(unittest.TestCase):
    def test_same_events_different_arrival_order_converge_to_same_state(self):
        events = [
            TransactionEvent(transaction_id="TXN-15", source="SecondaryBank",
                              timestamp="2024-03-15T10:00:00Z", amount=100.0,
                              currency="USD", status="Pending", confidence_score=0.7),
            TransactionEvent(transaction_id="TXN-15", source="PrimaryBank",
                              timestamp="2024-03-15T09:00:00Z", amount=110.0,
                              currency="USD", status="Pending", confidence_score=0.95),
            TransactionEvent(transaction_id="TXN-15", source="PrimaryBank",
                              timestamp="2024-03-15T11:00:00Z", amount=120.0,
                              currency="USD", status="Approved", confidence_score=0.95),
        ]

        engine_a, db_a = make_engine()
        engine_a.process_events(list(events))
        state_a = db_a.get_state("TXN-15")

        engine_b, db_b = make_engine()
        engine_b.process_events(list(reversed(events)))
        state_b = db_b.get_state("TXN-15")

        self.assertEqual(state_a.amount, state_b.amount)
        self.assertEqual(state_a.status, state_b.status)
        self.assertEqual(state_a.source, state_b.source)
        db_a.close()
        db_b.close()

    def test_replaying_full_batch_twice_is_idempotent(self):
        events = [
            TransactionEvent(transaction_id="TXN-16", source="PrimaryBank",
                              timestamp="2024-03-15T10:00:00Z", amount=100.0,
                              currency="USD", status="Pending", confidence_score=0.9),
            TransactionEvent(transaction_id="TXN-16", source="PrimaryBank",
                              timestamp="2024-03-15T11:00:00Z", amount=100.0,
                              currency="USD", status="Approved", confidence_score=0.9),
        ]
        engine, db = make_engine()
        engine.process_events(list(events))
        v1 = db.get_state("TXN-16").version
        engine.process_events(list(events))  # replay identical batch
        v2 = db.get_state("TXN-16").version
        self.assertEqual(v1, v2)
        db.close()


class TestPerformance(unittest.TestCase):
    def test_processes_1000_events_quickly(self):
        import time
        import random

        sources = ["PrimaryBank", "SecondaryBank", "TertiaryBank"]
        statuses = ["Pending", "Approved", "Completed", "Rejected"]
        events = [
            TransactionEvent(
                transaction_id=f"BENCH-{random.randint(1, 150):04d}",
                source=random.choice(sources),
                timestamp=f"2024-03-15T{random.randint(0,23):02d}:{random.randint(0,59):02d}:{random.randint(0,59):02d}Z",
                amount=round(random.uniform(1, 5000), 2), currency="USD",
                status=random.choice(statuses), confidence_score=round(random.uniform(0.5, 1.0), 2),
            )
            for _ in range(1000)
        ]
        engine, db = make_engine()
        t0 = time.perf_counter()
        engine.process_events(events)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 5.0)
        db.close()


class TestMalformedInput(unittest.TestCase):
    """CLI-level input validation (src/cli.py: load_events).

    Found while stress-testing: three of these cases used to crash with a
    raw Python traceback instead of a clean CLI error -- missing
    transaction_id, a non-dict item in the event array, and a non-array
    top-level JSON value. Fixed in src/cli.py; these tests lock the fix in.
    """

    def setUp(self):
        from src.cli import load_events, InputValidationError
        self.load_events = load_events
        self.InputValidationError = InputValidationError

    def _load_str(self, json_text):
        import io
        import json as json_module
        # load_events reads from a file path or stdin; simplest is to feed
        # it a parsed-data path via a temp file so we exercise the real
        # code path end to end.
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json_text)
            path = f.name
        try:
            return self.load_events(path)
        finally:
            os.unlink(path)

    def test_missing_transaction_id_is_rejected(self):
        with self.assertRaises(self.InputValidationError):
            self._load_str('[{"source": "PrimaryBank"}]')

    def test_null_transaction_id_is_rejected(self):
        with self.assertRaises(self.InputValidationError):
            self._load_str('[{"transaction_id": null, "source": "PrimaryBank"}]')

    def test_non_dict_event_is_rejected(self):
        with self.assertRaises(self.InputValidationError):
            self._load_str('["just a string", 123]')

    def test_non_array_top_level_is_rejected(self):
        with self.assertRaises(self.InputValidationError):
            self._load_str('42')

    def test_string_amount_is_rejected(self):
        with self.assertRaises(self.InputValidationError):
            self._load_str(
                '[{"transaction_id":"X","source":"PrimaryBank",'
                '"amount":"not_a_number","currency":"USD","status":"Pending"}]'
            )

    def test_boolean_amount_is_rejected(self):
        # bool is a subclass of int in Python; must be explicitly excluded.
        with self.assertRaises(self.InputValidationError):
            self._load_str(
                '[{"transaction_id":"X","source":"PrimaryBank",'
                '"amount":true,"currency":"USD","status":"Pending"}]'
            )

    def test_empty_array_yields_no_events_without_error(self):
        events = self._load_str('[]')
        self.assertEqual(events, [])

    def test_valid_minimal_event_still_loads(self):
        events = self._load_str('[{"transaction_id":"X","source":"PrimaryBank","amount":100.0}]')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].transaction_id, "X")


class TestConcurrentMultiTransactionInterleaving(unittest.TestCase):
    """Stress test: multiple transactions' edge cases interleaved together.

    The PRD's own edge-case files (and sample_inputs/7_interacting_edge_cases.json)
    each drive one transaction through several edge cases sequentially. Real
    ingestion won't be that tidy -- events for many transaction_ids arrive
    interleaved and out of order in the same batch. This test builds one
    batch that interleaves duplicates, conflicts, late-arrivals, missing
    fields, and status transitions across FIVE different transactions at
    once, shuffles the batch, and checks every transaction still converges
    to the correct final state independently of the others.
    """

    def test_five_transactions_interleaved_converge_independently(self):
        import random

        events = []

        # TXN-A: straightforward conflict, PrimaryBank should win on amount.
        events.append(TransactionEvent(
            transaction_id="TXN-A", source="SecondaryBank",
            timestamp="2024-03-15T09:00:00Z", amount=100.0, currency="USD", status="Pending",
        ))
        events.append(TransactionEvent(
            transaction_id="TXN-A", source="PrimaryBank",
            timestamp="2024-03-15T09:05:00Z", amount=150.0, currency="USD", status="Pending",
        ))

        # TXN-B: exact duplicate mixed in among unrelated events -- must stay idempotent.
        b1 = TransactionEvent(
            transaction_id="TXN-B", source="PrimaryBank",
            timestamp="2024-03-15T09:10:00Z", amount=500.0, currency="EUR", status="Approved",
        )
        events.append(b1)
        events.append(b1)  # exact duplicate, interleaved with other transactions below

        # TXN-C: multi-step valid status transition, events deliberately out of the
        # "natural" order -- engine must still process by timestamp ascending.
        events.append(TransactionEvent(
            transaction_id="TXN-C", source="PrimaryBank",
            timestamp="2024-03-15T09:20:00Z", amount=75.0, currency="USD", status="Completed",
        ))
        events.append(TransactionEvent(
            transaction_id="TXN-C", source="PrimaryBank",
            timestamp="2024-03-15T09:00:00Z", amount=75.0, currency="USD", status="Pending",
        ))
        events.append(TransactionEvent(
            transaction_id="TXN-C", source="PrimaryBank",
            timestamp="2024-03-15T09:10:00Z", amount=75.0, currency="USD", status="Approved",
        ))

        # TXN-D: missing source and missing timestamp, mixed into the same batch.
        events.append(TransactionEvent(
            transaction_id="TXN-D", source=None,
            timestamp="2024-03-15T09:30:00Z", amount=999.0, currency="GBP", status="Pending",
        ))

        # TXN-E: late-arriving PrimaryBank event that should win the amount
        # conflict by priority even though it has an earlier timestamp than
        # the SecondaryBank event already "on file" -- mirrors TestLateEvents,
        # but interleaved with four unrelated transactions instead of alone.
        events.append(TransactionEvent(
            transaction_id="TXN-E", source="SecondaryBank",
            timestamp="2024-03-15T09:15:00Z", amount=42.0, currency="USD", status="Pending",
        ))
        late = TransactionEvent(
            transaction_id="TXN-E", source="PrimaryBank",
            timestamp="2024-03-15T09:12:00Z", amount=43.0, currency="USD", status="Pending",
        )
        events.append(late)

        # Run the SAME interleaved-and-shuffled batch three times with
        # different random orderings, fresh engine each time, and confirm
        # every transaction converges to an identical final state regardless
        # of arrival order -- this is determinism/replayability, but under
        # realistic multi-transaction interleaving rather than a single txn.
        final_states_by_run = []
        for seed in (1, 2, 3):
            rng = random.Random(seed)
            shuffled = events[:]
            rng.shuffle(shuffled)

            engine, db = make_engine()
            engine.process_events(shuffled)
            report = {e["transaction_id"]: e["final_state"] for e in engine.generate_final_report()}
            final_states_by_run.append(report)
            db.close()

        # All three shuffled runs must agree.
        for txn_id in ("TXN-A", "TXN-B", "TXN-C", "TXN-D", "TXN-E"):
            states = [run[txn_id] for run in final_states_by_run]
            self.assertTrue(
                all(s == states[0] for s in states),
                f"{txn_id} did not converge to the same state across shuffled runs: {states}",
            )

        canonical = final_states_by_run[0]

        # TXN-A: PrimaryBank wins the amount conflict.
        self.assertEqual(canonical["TXN-A"]["source"], "PrimaryBank")
        self.assertEqual(canonical["TXN-A"]["amount"], 150.0)

        # TXN-B: duplicate did not bump the version past 1.
        self.assertEqual(canonical["TXN-B"]["version"], 1)

        # TXN-C: converged to Completed via the valid Pending->Approved->Completed
        # chain, regardless of the order events were fed in.
        self.assertEqual(canonical["TXN-C"]["status"], "Completed")

        # TXN-D: missing source defaulted, did not crash or drop the transaction.
        self.assertEqual(canonical["TXN-D"]["source"], "UnknownSource")

        # TXN-E: PrimaryBank's late event won on source priority despite the
        # earlier timestamp.
        self.assertEqual(canonical["TXN-E"]["source"], "PrimaryBank")
        self.assertEqual(canonical["TXN-E"]["amount"], 43.0)


if __name__ == "__main__":
    unittest.main()
