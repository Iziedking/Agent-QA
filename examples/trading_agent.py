#!/usr/bin/env python3
"""A minimal single-purpose trading agent with portable memory.

Paper trades only: the market is simulated, no funds exist, and nothing here
executes on any exchange. What is real is the memory. This is the builder
pattern from docs/agent-setup.md, runnable end to end:

1. On start, recall the strategy folder: the agent wakes up knowing its
   position and its own trade history, even on a machine it has never run on.
2. Decide, with that history in view. Here the decision is a toy rule; in a
   real agent the recalled notes would be pasted into the model's prompt,
   which is how a model "remembers": memory is context, not a model feature.
3. Execute, then remember the action immediately, and show the Walrus receipt
   the way a real agent would show it in its activity log.
4. Before exiting, remember one position digest that supersedes the last.

Configuration comes from the environment, the way a packaged agent would keep
it next to its other credentials after asking the user once:

    AGENT_MEMORY_USER        the identity, e.g. you@example.com
    AGENT_MEMORY_PASSPHRASE  the passphrase (never printed, never stored here)
    AGENT_MEMORY_ENDPOINT    optional, defaults to https://agentsqa.xyz

Standard library only, so any builder can lift it whole.
"""

import json
import os
import re
import sys
import time
import urllib.request

ENDPOINT = os.environ.get("AGENT_MEMORY_ENDPOINT", "https://agentsqa.xyz").rstrip("/")
USER = os.environ.get("AGENT_MEMORY_USER", "")
PASSPHRASE = os.environ.get("AGENT_MEMORY_PASSPHRASE", "")
FOLDER = "dex-trading"
PAIR = "ETH/USDC"


def call(path: str, body: dict) -> dict:
    """One POST to the memory service. The passphrase travels only over HTTPS."""
    req = urllib.request.Request(
        f"{ENDPOINT}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def recall(query: str, limit: int = 5) -> dict:
    return call("/recall", {
        "user_key": USER, "passphrase": PASSPHRASE,
        "query": query, "folder": FOLDER, "limit": limit,
    })


def remember(content: str) -> dict:
    return call("/remember", {
        "user_key": USER, "passphrase": PASSPHRASE,
        "content": content, "folder": FOLDER,
    })


def main() -> None:
    if not USER or not PASSPHRASE:
        print("Set AGENT_MEMORY_USER and AGENT_MEMORY_PASSPHRASE first.")
        print("A packaged agent asks for these once in its settings screen.")
        sys.exit(1)

    today = time.strftime("%Y-%m-%d")
    print(f"[trading-agent] waking up · pair {PAIR} · memory folder {FOLDER}")

    # 1. Recall state. The locked guard matters here: a wrong passphrase must
    #    stop the agent, not let it trade as if it had no history.
    state = recall("position digest holding cash")
    if state.get("locked"):
        print("[trading-agent] memory holds notes this passphrase does not open.")
        print("[trading-agent] refusing to run: fix the passphrase first.")
        sys.exit(1)

    holding, cash = 0.0, 2000.0
    digest = next((r for r in state.get("records", []) if "position digest" in r.lower()), None)
    if digest:
        m = re.search(r"holding=(\d+(?:\.\d+)?) cash=(\d+(?:\.\d+)?)", digest)
        if m:
            holding, cash = float(m.group(1)), float(m.group(2))
        print(f"[trading-agent] remembered state: holding={holding} ETH, cash={cash} USDC")
    else:
        print("[trading-agent] no position digest in memory: starting fresh.")

    history = recall(f"trades on {PAIR} and how they went", limit=3)
    for note in history.get("records", []):
        if "position digest" not in note.lower():
            print(f"[trading-agent] history: {note[:96]}...")

    # 2. Decide. A toy price feed and a toy rule; a real agent would put the
    #    recalled history into its model's prompt right here.
    price = 3800 + (int(time.time()) % 120)
    if holding > 0:
        action, size = "SELL", holding
        reason = f"closing the open position from memory at {price}"
        cash += size * price
        holding = 0.0
    else:
        action = "BUY"
        size = round(cash * 0.25 / price, 4)
        reason = f"opening a starter position, 25 percent of cash at {price}"
        cash -= size * price
        holding = size

    # 3. Execute (paper) and remember the action the moment it happens.
    print(f"[trading-agent] {action} {size} ETH at {price} USDC · {reason}")
    trade = remember(
        f"{today}: paper {action} {size} ETH at {price} USDC on {PAIR}. "
        f"Reason: {reason}. Portfolio after: {round(holding, 4)} ETH, {round(cash, 2)} USDC."
    )
    if trade.get("stored"):
        print(f"[trading-agent] trade remembered · receipt {trade.get('receipt', '?')}")
    else:
        print(f"[trading-agent] WARNING trade note not confirmed: {trade.get('note')}")

    # 4. One digest before exiting, so the next run (any machine) wakes up here.
    d = remember(
        f"{today}: position digest holding={round(holding, 4)} cash={round(cash, 2)}. "
        f"Last action: {action} {size} {PAIR} at {price}."
    )
    if d.get("stored"):
        print(f"[trading-agent] digest remembered · receipt {d.get('receipt', '?')}")
    print("[trading-agent] done. Run me again anywhere and I pick up from here.")


if __name__ == "__main__":
    main()
