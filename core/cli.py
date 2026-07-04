"""Command-line entry point: ``agent-qa <url>``.

Runs the reliability engine against an MCP endpoint and prints the report as
readable text (default) or JSON (``--json``). Useful for local development and
for the demo before the FastAPI / MCP layers exist.
"""

from __future__ import annotations

import argparse
import json
import sys

from .report import evaluate_sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-qa",
        description="Automated reliability report for a public MCP endpoint.",
    )
    parser.add_argument("url", help="The MCP endpoint URL to evaluate.")
    parser.add_argument(
        "--json", action="store_true", help="Emit the report as JSON."
    )
    args = parser.parse_args(argv)

    report = evaluate_sync(args.url)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_text())

    # Non-zero exit if the endpoint was unreachable, so scripts can gate on it.
    return 0 if report.reachable else 1


if __name__ == "__main__":
    sys.exit(main())
