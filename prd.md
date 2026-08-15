# Temporal Conflict Resolution in Multi-Source Financial Transaction Records

# Temporal Conflict Resolution in Multi-Source Financial Transaction Records

Title:
Temporal Conflict Resolution in Multi-Source Financial Transaction Records

Background:
You are a data engineer at a fintech startup integrating transaction data from multiple legacy banking systems. Each system emits transaction records with inconsistent schemas, timestamps, and reconciliation logic. Your team uses Oracle DB for transactional storage and Python for analytics pipelines. The data arrives asynchronously and may be duplicated, reordered, or contain conflicting values across sources. Your task is to build a deterministic reconciliation engine that processes incoming events, resolves conflicts based on business rules, and maintains an accurate, auditable state of all financial records.

Problem Statement:
You must design and implement a system that ingests transaction records from multiple sources with overlapping timestamps and conflicting data fields (e.g., amount, currency, status). The system must process these records in real-time, detect and resolve conflicts using business logic, and maintain a consistent, versioned state of each transaction. Conflicting records may arrive out-of-order or duplicated, requiring idempotent processing and replayable decision-making.

Scope:
The system must support ingestion of transaction records from multiple sources, detect and resolve conflicts based on business rules, and maintain a consistent state. It must handle duplicates, out-of-order events, conflicting data, and support auditability. The final state must be deterministic, replayable, and reflect the most accurate view of transaction history.

MVP Scope:
Build a command-line reconciliation engine that:  
1. Accepts JSON-formatted transaction events via stdin or file input.  
2. Processes each event to update a transaction state in an in-memory database (SQLite or Oracle DB).  
3. Detects and resolves conflicts between overlapping records from different sources.  
4. Maintains a versioned state for each transaction with timestamps and source metadata.  
5. Outputs a final reconciliation decision and audit trail for each transaction.

Advanced/Bonus Scope:
Extend the system to support:  
- Real-time HTTP endpoint to receive transaction events.  
- Visualization of reconciliation decisions in a Jupyter Notebook dashboard.  
- Support for time-zone-aware timestamps and handling of midnight transitions.  
- Integration with Oracle DB via cx_Oracle for persistent state storage.

Functional Requirements:
- Input: JSON events with fields: `transaction_id`, `source`, `timestamp`, `amount`, `currency`, `status`, `reconciliation_notes`.  
- Processing:  
  - Events must be processed in order of `timestamp` (ascending).  
  - If a transaction_id appears multiple times, the system must detect and resolve conflicts.  
  - Conflicts are defined as differing values in `amount`, `currency`, or `status`.  
  - Resolution logic:  
    - Prefer `source = 'PrimaryBank'` over others.  
    - If both sources are equal, prefer the one with higher `confidence_score` (if present).  
    - If both are equal, prefer the one with the latest `timestamp`.  
    - If `timestamp` is ambiguous (same second), prefer the one with the earliest `event_sequence` (if provided).  
  - Maintain a versioned state for each transaction: each update must be stored with `version`, `timestamp`, `source`, and `fields_changed`.  
  - Output:  
    - Final reconciliation decision per transaction_id.  
    - Audit trail: list of all events considered, conflicts detected, and resolution applied.  
  - Support for:  
    - Duplicate events (same `transaction_id`, `source`, `timestamp`, `fields`) — must be idempotent.  
    - Late events arriving after resolution — must replay and update state if needed.  
    - Missing `timestamp` — use system time with warning.  
    - Missing `source` — use default `UnknownSource`.  
    - Conflicting `status` values — resolve using business logic.  
    - `status` transitions violating business rules (e.g., `Pending` → `Completed` without `Approved`) — flag as invalid.

Non-Functional Requirements:
- Deterministic: Same input → same final state and audit output.  
- Idempotent: Duplicate events must not change state.  
- Replayable: Events can be replayed in any order; system must reconstruct correct state.  
- Auditability: All decisions and state transitions must be traceable.  
- Performance: Process 1000+ events in under 5 seconds on a standard laptop.  
- Memory: Use SQLite or Oracle DB for state; avoid storing full history in memory.

Constraints:
- Use only Python, SQL (Oracle DB), Jupyter Notebook, Git, VS Code, Linux (Essentials), Google Colab.  
- Do not use external APIs, Kafka, or distributed systems.  
- No ML/LLM components.  
- All logic must be deterministic and rule-based.  
- Do not assume network availability; run locally.  
- Use only provided tools; no additional libraries beyond standard Python + cx_Oracle.

Deliverables:
1. Submission — Public GitHub repository URL (required).  
2. Repository contents —  
   - Backend CLI tool to process events.  
   - Sample input files covering ≥5 interacting edge cases.  
   - Audit output files showing reconciliation decisions and traceability.  
   - SQLite or Oracle DB schema and connection script.  
   - Jupyter Notebook with visualization of reconciliation results.  
3. Test Suite — Automated tests covering:  
   - Duplicate events.  
   - Conflicting `amount`, `currency`, `status`.  
   - Late events arriving after resolution.  
   - Missing `timestamp` or `source`.  
   - Invalid `status` transitions.  
   - Midnight transition handling.  
4. Documentation — README with clone → setup → run → test instructions, including how to run the CLI and view audit outputs.
