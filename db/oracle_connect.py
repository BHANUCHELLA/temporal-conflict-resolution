"""
Optional Oracle DB connection helper (bonus scope).

The MVP engine uses SQLite (stdlib `sqlite3`, zero external dependencies),
which is the primary, always-working path for this submission. This module
is an *additive* bonus: it lets `src.database.Database`-shaped state be
persisted to Oracle instead, via `cx_Oracle`, for teams that already run
Oracle in production (as described in the PRD background).

Usage:
    pip install cx_Oracle
    python db/oracle_connect.py --dsn "host:port/service" --user USER --password PASS

This only opens a connection and applies db/oracle_schema.sql; it does not
replace src/database.py. Wiring ReconciliationEngine to Oracle instead of
SQLite is a drop-in swap: implement the same method surface as
src.database.Database (get_state, save_state, log_event, log_conflict,
log_version, log_audit, get_all_states, close) backed by cx_Oracle cursors.
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="Apply the Oracle schema and verify connectivity.")
    parser.add_argument("--dsn", help="e.g. 'localhost:1521/XEPDB1' (not required with --dry-run)")
    parser.add_argument("--user", help="not required with --dry-run")
    parser.add_argument("--password", help="not required with --dry-run")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Parse and structurally validate oracle_schema.sql WITHOUT connecting to a "
            "database. No Oracle instance was available in the environment this project "
            "was built in, so this is the offline check that was actually run -- it is not "
            "a substitute for applying the schema against a real instance. See "
            "ARCHITECTURE.md 'Known simplifications'."
        ),
    )
    args = parser.parse_args()

    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oracle_schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        raw = f.read()
    # Strip '--' line comments BEFORE splitting on ';' -- a comment block with
    # no semicolon inside it would otherwise merge with the next real
    # statement and get misclassified as "starts with --" or dropped whole.
    no_comments = "\n".join(
        line for line in raw.splitlines() if not line.strip().startswith("--")
    )
    statements = [s.strip() for s in no_comments.split(";") if s.strip()]

    if args.dry_run:
        _dry_run_validate(schema_path, raw, statements)
        return

    if not (args.dsn and args.user and args.password):
        parser.error("--dsn, --user, and --password are required unless --dry-run is given")

    try:
        import cx_Oracle
    except ImportError:
        print(
            "cx_Oracle is not installed. This is an optional bonus dependency -- "
            "install it with `pip install cx_Oracle` to use Oracle DB persistence.",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = cx_Oracle.connect(user=args.user, password=args.password, dsn=args.dsn)
    cur = conn.cursor()
    for stmt in statements:
        cur.execute(stmt)
    conn.commit()
    cur.close()
    conn.close()
    print(f"Applied {len(statements)} DDL statements from {schema_path} to {args.dsn}.")


def _dry_run_validate(schema_path, raw_sql, statements):
    """Offline structural checks on oracle_schema.sql (no DB connection).

    This catches a real (if narrow) class of bugs -- mismatched parens,
    duplicate table names, a stray statement that doesn't start with a
    known DDL keyword -- without needing network access or an Oracle
    instance, neither of which is available in this environment.
    """
    problems = []

    code_only = "\n".join(
        line for line in raw_sql.splitlines() if not line.strip().startswith("--")
    )
    if code_only.count("(") != code_only.count(")"):
        problems.append(
            f"Unbalanced parentheses: {code_only.count('(')} '(' vs {code_only.count(')')} ')'"
        )

    known_starts = ("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX", "ALTER TABLE")
    table_names = []
    for stmt in statements:
        upper = stmt.upper().lstrip()
        if not upper.startswith(known_starts):
            problems.append(f"Statement does not start with a known DDL keyword: {stmt[:60]!r}...")
            continue
        if upper.startswith("CREATE TABLE"):
            name = stmt.split()[2].split("(")[0].strip()
            table_names.append(name)

    dupes = {t for t in table_names if table_names.count(t) > 1}
    if dupes:
        problems.append(f"Duplicate CREATE TABLE for: {sorted(dupes)}")

    print(f"Parsed {len(statements)} statements ({len(table_names)} tables) from {schema_path}")
    for t in table_names:
        print(f"  - {t}")

    if problems:
        print("\nStructural problems found:", file=sys.stderr)
        for p in problems:
            print(f"  ! {p}", file=sys.stderr)
        sys.exit(1)

    print(
        "\nNo structural problems found. This only validates syntax shape offline -- "
        "it does NOT confirm the schema applies cleanly to a real Oracle instance "
        "(reserved words, IDENTITY column support by edition, tablespace/quota issues, "
        "etc. can still only be caught by running it against Oracle)."
    )


if __name__ == "__main__":
    main()
