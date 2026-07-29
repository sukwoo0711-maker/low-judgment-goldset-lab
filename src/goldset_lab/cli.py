"""Command-line validators for portable evaluation artifacts."""
from __future__ import annotations

import argparse
from pathlib import Path

from .contracts import (
    ContractError,
    load_jsonl,
    scan_canary,
    validate_atomic_label,
    validate_candidate,
    validate_public_fixture,
)


def _validate(path: Path, kind: str) -> int:
    validator = {
        "fixture": validate_public_fixture,
        "label": validate_atomic_label,
        "candidate": validate_candidate,
    }[kind]
    rows = load_jsonl(path)
    for row in rows:
        validator(row)
    print(f"valid {kind}: {len(rows)} rows")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("kind", choices=("fixture", "label", "candidate"))
    validate.add_argument("path", type=Path)
    canary = sub.add_parser("scan-canary")
    canary.add_argument("canary")
    canary.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _validate(args.path, args.kind)
        hits = scan_canary(args.canary, args.paths)
        if hits:
            print("canary leak: " + ", ".join(hits))
            return 2
        print("canary absent")
        return 0
    except ContractError as exc:
        print(f"contract error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
