#!/usr/bin/env python3
"""Render `claude -p --output-format stream-json` events as live, readable output.

Reads JSONL from stdin, prints assistant text and tool activity as it happens.
Exits non-zero if the final result event reports an error.
"""

import json
import sys


def main() -> int:
    exit_code = 0
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            print(line, flush=True)
            continue

        etype = evt.get("type")
        if etype == "assistant":
            for blk in evt.get("message", {}).get("content", []):
                if blk.get("type") == "text" and blk.get("text"):
                    print(blk["text"], flush=True)
                elif blk.get("type") == "tool_use":
                    name = blk.get("name", "?")
                    inp = blk.get("input") or {}
                    hint = inp.get("file_path") or inp.get("command") or inp.get("pattern") or ""
                    hint = str(hint).replace("\n", " ")
                    if len(hint) > 100:
                        hint = hint[:100] + "…"
                    print(f"  [tool] {name} {hint}", flush=True)
        elif etype == "result":
            cost = evt.get("total_cost_usd") or 0.0
            turns = evt.get("num_turns", "?")
            dur_s = (evt.get("duration_ms") or 0) / 1000
            print(
                f"  [result] turns={turns} cost=${cost:.2f} duration={dur_s:.0f}s",
                flush=True,
            )
            if evt.get("is_error"):
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
