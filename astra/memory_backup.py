"""Local SQLite memory snapshots and read-only recovery verification."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from .archive import MEMORY_COLUMNS, SCHEMA_VERSION


def _read_only(path: Path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def _verify(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise ValueError(f"Expected memory schema {SCHEMA_VERSION}, found {version}; migrate with ASTRA first")
    columns = {row[1] for row in db.execute("PRAGMA table_info(memories)")}
    if not set(MEMORY_COLUMNS.split(", ")) <= columns:
        raise ValueError("Incomplete memory schema")
    checks = [row[0] for row in db.execute("PRAGMA integrity_check")]
    if checks != ["ok"]:
        raise ValueError("SQLite integrity check failed: " + "; ".join(checks[:5]))
    return {
        "schema_version": version,
        "memory_count": db.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "integrity": "ok",
    }


def verify_snapshot(path: Path):
    with closing(_read_only(Path(path))) as db:
        db.execute("BEGIN")
        return _verify(db)


def create_snapshot(source: Path, destination: Path):
    source, destination = Path(source), Path(destination)
    with closing(_read_only(source)) as db:
        # Reserve only a new destination; never open an existing file for writing.
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        complete = False
        try:
            deadline = time.monotonic() + 30

            def progress(_status, _remaining, _total):
                if time.monotonic() > deadline:
                    raise TimeoutError("Memory snapshot exceeded its 30-second copy deadline")

            with closing(sqlite3.connect(destination, timeout=5)) as snapshot:
                db.backup(snapshot, pages=128, progress=progress, sleep=0.1)
            result = verify_snapshot(destination)
            with destination.open("r+b") as saved:
                os.fsync(saved.fileno())
            complete = True
            return result
        finally:
            if not complete:
                destination.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="Copy memory into a new SQLite file (never overwrites)")
    snapshot.add_argument("source", type=Path)
    snapshot.add_argument("destination", type=Path)
    verify = commands.add_parser("verify", help="Read-only integrity/schema verification")
    verify.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        result = (create_snapshot(args.source, args.destination)
                  if args.command == "snapshot" else verify_snapshot(args.path))
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Memory {args.command} failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
