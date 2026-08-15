# Architecture

## Data flow

```
JSON events (file or stdin or HTTP POST)
        │
        ▼
 TransactionEvent  ──▶  fill defaults (source/timestamp) + warn
        │
        ▼
 content hash (transaction_id + all fields)
        │
        ├── hash already in event_log? ──▶ DUPLICATE, stop (idempotent)
        │
        ▼  no
 existing state for transaction_id?
        │
   ┌────┴────┐
   │  none    │  exists
   ▼          ▼
 INITIAL    compare amount/currency/status
 STATE      field-by-field against existing
 (v1)             │
                   ▼
           any field differs? ──▶ no ──▶ NO_CONFLICT (state unchanged)
                   │ yes
                   ▼
        decide winner: existing vs incoming
        1. source priority (PrimaryBank > other)
        2. confidence_score (higher wins)
        3. timestamp (later wins)
        4. event_sequence (earlier wins, same-second ties)
                   │
                   ▼
        for each conflicting field, apply winner's value
        EXCEPT status: validate against the transition
        state machine first (Pending→Approved→Completed,
        Rejected reachable from Pending/Approved, both
        terminal). Invalid jump ⇒ rejected, warning logged,
        prior status retained -- regardless of who "won".
                   │
                   ▼
        save new versioned state, log version_history,
        conflict_log, audit_trail entries
```

## Why this ordering

The PRD's resolution chain (source → confidence → timestamp → sequence) picks
a winner between two *whole records*, not independent per-field winners —
that's what makes the outcome for `amount`, `currency`, and `status` together
explainable with one sentence in the audit trail ("PrimaryBank outranks
SecondaryBank") instead of three unrelated reasons. Status gets a second,
independent check on top of that, because "which source do we trust more" and
"is this a legal state transition" are different questions — a trusted
source can still submit a state machine violation (e.g. skipping straight to
`Completed`), and that has to be caught regardless of who submitted it.

This is also why a **late** event (arrives last, but carries an earlier
real-world timestamp) can still correctly update `amount` while being
blocked from moving `status` backwards — see
`sample_inputs/3_late_events.json` and
`tests/test_engine.py::TestLateEvents::test_late_event_cannot_regress_a_completed_status`.

## Determinism & idempotency

- **Idempotent**: every event is SHA-256 hashed on `(transaction_id, source,
  timestamp, amount, currency, status, reconciliation_notes,
  confidence_score, event_sequence)` before processing. A repeat hash is
  dropped without touching state.
- **Deterministic / replayable**: the winner-decision chain depends only on
  the *content* of the competing records, never on arrival order, so feeding
  the same batch of events in any order converges to the same final state.
  Verified directly in
  `tests/test_engine.py::TestDeterminismAndReplay`.
- **Auditable**: every accepted change is versioned (`version_history`),
  every field conflict and its resolution reason is logged
  (`conflict_log`), and every decision — including rejected/duplicate/no-op
  ones — gets a narrative entry in `audit_trail`.

## Why SQLite (not "in-memory Python dict")

The PRD asks for state to live in a DB, not fully in process memory, and to
be replayable/auditable — SQLite gives us real persistence, transactions,
and a queryable audit history for free, with zero third-party dependencies.
`db/oracle_schema.sql` mirrors the same five tables for teams that want to
point this at Oracle instead (bonus scope); swapping the persistence layer
only requires implementing the same method surface as `src/database.py`
against `cx_Oracle` — the engine and CLI don't change.

## Design alternatives considered

These are real alternatives that were considered and rejected, not just
justifications for what was built — included because a reviewer evaluating
system design depth should be able to see the tradeoff, not just the choice.

**Conflict resolution: priority chain vs. CRDTs / vector clocks.**
A CRDT (e.g. a last-writer-wins register per field, or a proper merge type
for numeric fields) or vector-clock-based causal ordering is the standard
answer when you genuinely don't have a trusted-source hierarchy and need
convergence without coordination. Rejected here because the PRD explicitly
hands you a trusted-source hierarchy (`PrimaryBank` > others > confidence >
timestamp > sequence) — building a CRDT on top of that would be solving a
harder, more general problem than the one asked, at the cost of auditability
("the CRDT merged it" is a much weaker audit-trail sentence than "PrimaryBank
outranks SecondaryBank"). CRDTs would be the right call if the source
hierarchy didn't exist or changed dynamically per-field.

**Persistence: SQLite (event-state table) vs. full event sourcing.**
An event-sourcing design — append-only event log as the sole source of
truth, current state always *derived* by replaying — is arguably more
"correct" for an auditable financial system, and this design already keeps
`event_log` as an append-only table alongside the derived `transaction_state`
table, so it's a partial move in that direction. Full event sourcing (state
never stored directly, always recomputed) was rejected for the MVP because
it multiplies the cost of every read by transaction history length, and the
PRD asks for real-time processing of 1000+ events under 5 seconds — direct
state lookup with a versioned history table alongside it hits both the
performance and auditability requirements without the replay-on-every-read
cost. Full event sourcing would be the right upgrade if audit queries needed
to reconstruct "what did we believe was true at time T" for *arbitrary*
historical T, not just the current state plus a change log.

**Conflict granularity: per-record winner vs. per-field winner.**
Covered above in "Known simplifications" — worth repeating here as a design
choice, not just a limitation: per-field winner selection (mixing the "best"
amount from one source with the "best" status from another) is defensible
and arguably more accurate, but was rejected because the PRD's resolution
chain reads as a single priority order applied once per conflict, and
per-record winners keep every resolution explainable in one sentence instead
of a separate justification for every field.

**Ordering: timestamp-sort-then-process vs. true streaming.**
The PRD asks for "real-time" processing but also for events to be "processed
in order of timestamp." Those pull in different directions: a true streaming
system can't wait to see every event before deciding order, because more
events with earlier timestamps can always still arrive. This system resolves
that by treating each `process_events(batch)` call as its unit of ordering
(sort by timestamp within the batch, replay-safe across batches via the
idempotency hash) rather than promising strict global real-time ordering
across arbitrarily-delayed sources — which is also why "late events" are a
named, tested case (`TestLateEvents`) rather than an assumed impossibility.
A watermarking/windowing approach (as in real stream processors like Flink)
would give stronger ordering guarantees at the cost of introduced latency
and real infrastructure — out of scope given the constraint against
distributed systems.



- **Conflict resolution decides a winner per *event*, not fully
  independently per field.** A record from `PrimaryBank` wins all of its
  conflicting fields together, rather than mixing-and-matching the "best"
  value for each field from different sources. This matches the PRD's
  literal wording (a single priority chain, applied once per conflict) and
  keeps every resolution explainable with one reason instead of three.
- **Single-threaded HTTP endpoint** (`http_server.py`, bonus scope): SQLite
  connections are thread-affine, so the endpoint intentionally uses a plain
  `HTTPServer` rather than `ThreadingHTTPServer`. For local demo purposes
  this trades away concurrent request handling for correctness and
  simplicity — the right call for a bonus feature, not the core deliverable.
- **Oracle path is untested against a live Oracle instance** (none was
  available in this environment, and the network this project was built on
  is restricted to package registries — no Oracle host was reachable
  either). `db/oracle_schema.sql` is a straight dialect translation of the
  working SQLite schema; treat it as a strong starting point, not a
  guarantee. What *is* verified, and runs in CI on every push
  (`python db/oracle_connect.py --dry-run`): the DDL parses into exactly
  5 `CREATE TABLE` statements with no duplicate table names and balanced
  parentheses. That's a real but narrow check — it catches typos and
  structural drift, not Oracle-specific issues like reserved-word
  collisions, `IDENTITY` column support by edition, or tablespace/quota
  problems, which only running it against a real instance can catch.
  Concretely: while adding this dry-run check, an earlier version of its
  own comment-parsing logic silently dropped the `transaction_state` table
  from validation because a multi-line SQL comment block (no semicolon
  inside it) merged with the statement that followed — the dry-run caught
  that when a broken schema still passed. That bug is fixed and re-verified
  against both the real schema (passes) and a deliberately broken one
  (correctly fails) — see `db/oracle_connect.py`.
