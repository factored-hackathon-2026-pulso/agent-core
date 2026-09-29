"""CLI `agentcore`: `contracts [--check] [--out DIR]` y `validate <ruta> [--json]`."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_core.adapters.system_clock import SystemClock
from agent_core.contracts import check_contracts, write_contracts
from agent_core.flows.cli_validate import run_validate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentcore")
    sub = parser.add_subparsers(dest="command", required=True)
    contracts = sub.add_parser("contracts", help="genera o verifica contracts/")
    contracts.add_argument("--check", action="store_true", help="falla si contracts/ está desactualizado")
    contracts.add_argument("--out", type=Path, default=Path("contracts"))
    validate = sub.add_parser("validate", help="valida un registro de autoría (M1)")
    validate.add_argument("root", type=Path)
    validate.add_argument("--json", action="store_true", help="salida JSON estable")
    args = parser.parse_args(argv)
    if args.command == "contracts":
        if args.check:
            diffs = check_contracts(args.out)
            for rel in diffs:
                print(f"contracts desactualizado: {rel} (corre `uv run agentcore contracts`)",
                      file=sys.stderr)
            return 1 if diffs else 0
        write_contracts(args.out)
        return 0
    if args.command == "validate":
        clock = SystemClock()
        start = clock.monotonic_ns()
        code, output = run_validate(args.root, as_json=args.json)
        print(output)
        if not args.json:
            print(f"validado en {(clock.monotonic_ns() - start) // 1_000_000} ms", file=sys.stderr)
        return code
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
