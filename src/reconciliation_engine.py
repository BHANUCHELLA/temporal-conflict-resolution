"""
Deterministic, idempotent, replayable reconciliation engine.

Resolution priority chain (per PRD "Functional Requirements > Processing"):
    1. Prefer source == 'PrimaryBank' over any other source.
    2. If source priority ties, prefer the higher `confidence_score`.
    3. If that ties, prefer the later `timestamp`.
    4. If timestamps are ambiguous (same second), prefer the earlier `event_sequence`.
    5. If everything above ties exactly, the most-recently-processed event wins
       (keeps the engine total and deterministic for any input ordering).

Status transitions are additionally validated against a simple forward state
machine (Pending -> Approved -> Completed, with Rejected reachable from either
Pending or Approved). A proposed status change that skips a required step is
flagged as invalid and rejected -- the prior valid status is retained, and a
warning is recorded in the audit trail -- rather than corrupting the ledger.
"""

import datetime as dt
import hashlib
import json
from typing import List

from .models import TransactionEvent, TransactionState, ReconciliationResult

CONFLICT_FIELDS = ("amount", "currency", "status")

VALID_STATUS_TRANSITIONS = {
    None: {"Pending", "Approved", "Completed", "Rejected"},
    "Pending": {"Pending", "Approved", "Rejected"},
    "Approved": {"Approved", "Completed", "Rejected"},
    "Completed": {"Completed"},
    "Rejected": {"Rejected"},
}


class ReconciliationEngine:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _event_hash(event: TransactionEvent) -> str:
        payload = json.dumps(
            {
                "transaction_id": event.transaction_id,
                "source": event.source,
                "timestamp": event.timestamp,
                "amount": event.amount,
                "currency": event.currency,
                "status": event.status,
                "reconciliation_notes": event.reconciliation_notes,
                "confidence_score": event.confidence_score,
                "event_sequence": event.event_sequence,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _fill_defaults(event: TransactionEvent) -> List[str]:
        warnings = []
        if not event.source:
            event.source = "UnknownSource"
            warnings.append("Missing source; defaulted to 'UnknownSource'.")
        if not event.timestamp:
            event.timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            warnings.append(f"Missing timestamp; used current system time ({event.timestamp}).")
        return warnings

    @staticmethod
    def _source_priority(source) -> int:
        # 0 = highest priority. Only PrimaryBank gets special treatment;
        # every other source (SecondaryBank, TertiaryBank, UnknownSource, ...) ties.
        return 0 if source == "PrimaryBank" else 1

    @staticmethod
    def _parse_ts(ts):
        if not ts:
            return None
        try:
            return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _decide_winner(self, existing: TransactionState, incoming: TransactionEvent):
        """Returns (winner, reason) where winner is 'existing' or 'incoming'."""
        ep, ip = self._source_priority(existing.source), self._source_priority(incoming.source)
        if ep != ip:
            if ep < ip:
                return "existing", f"source priority: '{existing.source}' outranks '{incoming.source}'"
            return "incoming", f"source priority: '{incoming.source}' outranks '{existing.source}'"

        ec = existing.confidence_score if existing.confidence_score is not None else -1.0
        ic = incoming.confidence_score if incoming.confidence_score is not None else -1.0
        if ec != ic:
            if ec > ic:
                return "existing", f"higher confidence_score ({ec} > {ic})"
            return "incoming", f"higher confidence_score ({ic} > {ec})"

        et, it = self._parse_ts(existing.timestamp), self._parse_ts(incoming.timestamp)
        if et and it and et != it:
            if et > it:
                return "existing", f"later timestamp ({existing.timestamp} > {incoming.timestamp})"
            return "incoming", f"later timestamp ({incoming.timestamp} > {existing.timestamp})"

        # Same second (or unparsable) -> earliest event_sequence wins.
        es = existing.event_sequence if existing.event_sequence is not None else float("inf")
        is_ = incoming.event_sequence if incoming.event_sequence is not None else float("inf")
        if es != is_:
            if es < is_:
                return "existing", f"earlier event_sequence ({es} < {is_})"
            return "incoming", f"earlier event_sequence ({is_} < {es})"

        return "incoming", "fully tied on all criteria; latest-processed event applied"

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def process_event(self, event: TransactionEvent) -> ReconciliationResult:
        warnings = self._fill_defaults(event)
        event_hash = self._event_hash(event)

        inserted = self.db.log_event(event, event_hash, is_duplicate=False)
        if not inserted:
            existing = self.db.get_state(event.transaction_id)
            return ReconciliationResult(
                transaction_id=event.transaction_id,
                final_state=existing,
                warnings=warnings + ["Duplicate event ignored (idempotent)."],
                is_duplicate=True,
            )

        existing = self.db.get_state(event.transaction_id)

        # ---- first time we've seen this transaction_id ----
        if existing is None:
            new_state = TransactionState(
                transaction_id=event.transaction_id,
                version=1,
                source=event.source,
                timestamp=event.timestamp,
                amount=event.amount,
                currency=event.currency,
                status=event.status,
                reconciliation_notes=event.reconciliation_notes,
                confidence_score=event.confidence_score,
                event_sequence=event.event_sequence,
            )
            self.db.save_state(new_state)
            self.db.log_version(
                event.transaction_id, 1, event.source, event.timestamp,
                fields_changed=["amount", "currency", "status"], snapshot=new_state.to_dict(),
            )
            self.db.log_audit(
                event.transaction_id, "INITIAL_STATE",
                {"source": event.source, "timestamp": event.timestamp},
            )
            return ReconciliationResult(
                transaction_id=event.transaction_id, final_state=new_state,
                warnings=warnings, is_new=True,
            )

        # ---- detect field-level conflicts against current state ----
        conflicts = []
        for f in CONFLICT_FIELDS:
            existing_value = getattr(existing, f)
            incoming_value = getattr(event, f)
            if incoming_value is not None and incoming_value != existing_value:
                conflicts.append({
                    "field": f,
                    "existing_value": existing_value,
                    "incoming_value": incoming_value,
                    "existing_source": existing.source,
                    "incoming_source": event.source,
                })

        if not conflicts:
            self.db.log_audit(
                event.transaction_id, "NO_CONFLICT",
                {"source": event.source, "timestamp": event.timestamp},
            )
            return ReconciliationResult(
                transaction_id=event.transaction_id, final_state=existing, warnings=warnings,
            )

        winner, reason = self._decide_winner(existing, event)

        resolutions = []
        fields_changed = []
        new_values = {
            "source": existing.source, "timestamp": existing.timestamp,
            "amount": existing.amount, "currency": existing.currency, "status": existing.status,
            "reconciliation_notes": existing.reconciliation_notes,
            "confidence_score": existing.confidence_score, "event_sequence": existing.event_sequence,
        }

        for c in conflicts:
            f = c["field"]
            proposed_value = c["incoming_value"] if winner == "incoming" else c["existing_value"]

            if f == "status":
                old_status = existing.status
                if proposed_value != old_status:
                    allowed = VALID_STATUS_TRANSITIONS.get(old_status, set())
                    if proposed_value not in allowed:
                        warnings.append(
                            f"Invalid status transition rejected: '{old_status}' -> "
                            f"'{proposed_value}'. Retaining '{old_status}'."
                        )
                        self.db.log_conflict(
                            event.transaction_id, f, c["existing_value"], c["incoming_value"],
                            c["existing_source"], c["incoming_source"], existing.source,
                            "invalid status transition rejected",
                        )
                        continue  # do not apply this field's change

            new_values[f] = proposed_value
            fields_changed.append(f)
            resolution_source = event.source if winner == "incoming" else existing.source
            resolutions.append({"field": f, "resolved_value": proposed_value, "reason": reason})
            self.db.log_conflict(
                event.transaction_id, f, c["existing_value"], c["incoming_value"],
                c["existing_source"], c["incoming_source"], resolution_source, reason,
            )

        if fields_changed:
            if winner == "incoming":
                new_values["source"] = event.source
                new_values["timestamp"] = event.timestamp
                new_values["confidence_score"] = event.confidence_score
                new_values["event_sequence"] = event.event_sequence
            new_version = existing.version + 1
            new_state = TransactionState(
                transaction_id=event.transaction_id, version=new_version, **new_values
            )
            self.db.save_state(new_state)
            self.db.log_version(
                event.transaction_id, new_version, event.source, event.timestamp,
                fields_changed=fields_changed, snapshot=new_state.to_dict(),
            )
            self.db.log_audit(
                event.transaction_id, "CONFLICT_RESOLVED",
                {"fields_changed": fields_changed, "winner": winner, "reason": reason},
            )
            final_state = new_state
        else:
            self.db.log_audit(
                event.transaction_id, "CONFLICT_REJECTED",
                {"reason": "all proposed field changes were rejected (invalid transitions)"},
            )
            final_state = existing

        return ReconciliationResult(
            transaction_id=event.transaction_id, final_state=final_state,
            conflicts_detected=conflicts, resolutions_applied=resolutions, warnings=warnings,
        )

    def process_events(self, events: List[TransactionEvent]) -> List[ReconciliationResult]:
        """Processes events in ascending timestamp order, per the PRD.
        Events with a missing timestamp are assigned the current system time
        during processing (see _fill_defaults) and therefore naturally sort last.
        """
        def sort_key(e: TransactionEvent):
            parsed = self._parse_ts(e.timestamp) if e.timestamp else None
            return parsed or dt.datetime.max.replace(tzinfo=dt.timezone.utc)

        return [self.process_event(e) for e in sorted(events, key=sort_key)]

    def generate_final_report(self):
        report = []
        for state in self.db.get_all_states():
            txn = state.transaction_id
            report.append({
                "transaction_id": txn,
                "final_state": state.to_dict(),
                "events_considered": self.db.count_events(txn),
                "conflicts_detected": self.db.count_conflicts(txn),
                "audit_trail": self.db.get_audit_trail(txn),
            })
        return sorted(report, key=lambda r: r["transaction_id"])
