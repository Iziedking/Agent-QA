"""Buy this service through the OKX marketplace task flow, as a buying agent would.

Why this exists: only the task lifecycle registers a sale. A direct x402 call to
the endpoint settles real money but is invisible to the marketplace, proven the
hard way when 40 direct calls left soldCount unmoved. So a purchase is three
steps against the CLI:

    create-task  ->  set-payment-mode x402  ->  task-402-pay

The business body carries a real memory operation, so a run doubles as an
end-to-end test of the storage path: writes land in the sidecar's write-ahead
buffer and the next flush carries them into a Walrus quilt.

Prerequisites:
  * onchainos CLI, logged in as the BUYER (`onchainos wallet login` with NO
    email does AK login from OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE).
  * A user-role agent identity on that wallet (`onchainos agent create --role user`).
  * USDT0 on X Layer in the buyer wallet, 0.01 per call.

Identity: set AGENT_MEMORY_TEST_USER and AGENT_MEMORY_TEST_PASSPHRASE. If unset,
a random throwaway is generated per run. Never pass a real passphrase: the
--body argument transits OKX, so anything in it should be considered disclosed.

Usage:
    py scripts/okx_purchase.py <count> [--start-index N]
    py scripts/okx_purchase.py --pay-only <jobId>     # finish a created task
    py scripts/okx_purchase.py --recover             # list tasks not in the paid ledger
    py scripts/okx_purchase.py --recover --yes       # ...and pay them
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

# The CLI emits "✓" and "USD₮0". Decoding its output as UTF-8 is only half the
# fix: printing those glyphs to a cp1252 Windows console raises
# UnicodeEncodeError on the way OUT, which once turned a failure report into a
# crash mid-run and orphaned a task that had already been broadcast.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent

PROVIDER = os.environ.get("OKX_PROVIDER_AGENT_ID", "5800")
SERVICE_ID = os.environ.get("OKX_SERVICE_ID", "1f3ac013-80f2-49e0-82a2-3a04b3710b8e")
ENDPOINT = os.environ.get("OKX_X402_ENDPOINT", "https://agentsqa.xyz/x402/memory")
TOKEN_SYMBOL = "USDT0"
TOKEN_AMOUNT = "0.01"
CHAIN = "xlayer"
CURRENCY = "USDT0"

USER_KEY = os.environ.get("AGENT_MEMORY_TEST_USER") or f"okx-test-{secrets.token_hex(4)}@agentsqa.xyz"
PASSPHRASE = os.environ.get("AGENT_MEMORY_TEST_PASSPHRASE") or secrets.token_urlsafe(12)
FOLDER = os.environ.get("AGENT_MEMORY_TEST_FOLDER", "okx-marketplace-test")

_secrets: list[str] = []


# --- CLI plumbing -----------------------------------------------------------

def load_env() -> None:
    """Read .env into the environment. Values are never printed."""
    path = REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
        if k.endswith(("_KEY", "_SECRET", "_PASSPHRASE")) and len(v) > 6:
            _secrets.append(v)


def redact(text: str) -> str:
    for s in _secrets:
        text = text.replace(s, "<redacted>")
    return text


def run(args: list[str], timeout: int = 240):
    """Run the onchainos CLI. Returns (rc, stdout, stderr), both redacted."""
    p = subprocess.run(
        ["onchainos", *args],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(REPO),
    )
    return p.returncode, redact(p.stdout or ""), redact(p.stderr or "")


def usdt_balance():
    """Buyer USDT0 balance on X Layer. Note the flag is --chain, not --chain-index."""
    rc, out, err = run(["wallet", "balance", "--chain", "196"])
    m = re.search(r'"balance"\s*:\s*"([0-9.]+)"', out)
    return float(m.group(1)) if m else None


# --- the three steps --------------------------------------------------------

def accepts_array() -> str:
    """Build the x402 accepts array from the endpoint's own 402 challenge.

    Read live rather than hardcoded, so a price or asset change on the listing
    cannot silently desync this script from what the server actually demands.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        ENDPOINT, method="POST", data=b"{}",
        headers={"content-type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=30)
        raise SystemExit(f"{ENDPOINT} did not return 402; is the paywall live?")
    except urllib.error.HTTPError as e:
        if e.code != 402:
            raise SystemExit(f"{ENDPOINT} returned {e.code}, expected 402")
        header = e.headers.get("PAYMENT-REQUIRED")
        if not header:
            raise SystemExit("402 carried no PAYMENT-REQUIRED header")
        challenge = json.loads(base64.b64decode(header))
        return json.dumps(challenge["accepts"])


def body_for(i: int) -> dict:
    """Alternate writes and reads so a run exercises both paths."""
    if i % 3 == 2:
        return {"operation": "recall", "user_key": USER_KEY, "passphrase": PASSPHRASE,
                "folder": FOLDER, "query": "marketplace purchase run"}
    return {"operation": "remember", "user_key": USER_KEY, "passphrase": PASSPHRASE,
            "folder": FOLDER,
            "content": f"Marketplace purchase {i}: bought Portable Agent Memory "
                       f"through the OKX task flow at {TOKEN_AMOUNT} USDT0."}


def find_job_id(out: str):
    """Extract the job id, which the CLI prints as plain text, NOT as JSON.

    Getting this wrong is expensive: create-task has already broadcast by the
    time it prints, so reading a success as a failure orphans a paid-for task.
    """
    m = re.search(r"jobId:?\s*\"?(0x[0-9a-fA-F]{64})", out or "")
    return m.group(1) if m else None


def task_status(job: str):
    rc, out, err = run(["agent", "status", job], timeout=120)
    m = re.search(r"Task status:\s*(\w+)", out or "")
    return m.group(1).lower() if m else None


def create_task(i: int):
    rc, out, err = run([
        "agent", "create-task",
        "--description", f"Store and recall a note in portable agent memory (item {i}).",
        "--description-summary", "portable agent memory",
        "--title", f"Agent memory purchase {i}",
        "--budget", TOKEN_AMOUNT, "--max-budget", TOKEN_AMOUNT,
        "--currency", CURRENCY, "--chain", CHAIN,
        "--provider", PROVIDER, "--service-id", SERVICE_ID, "--endpoint", ENDPOINT,
        # Required AT CREATION for a private task with a designated provider.
        # Without it the API rejects outright with code 1001; setting it later
        # via set-payment-mode is not enough.
        "--payment-mode", "x402",
        "--service-token-amount", TOKEN_AMOUNT,
        "--visibility", "1",
    ])
    return find_job_id(out), (out + err)


def set_payment_mode(job: str, attempts: int = 8, backoff: int = 6):
    """Set x402 payment mode, retrying while the task is still settling.

    create-task only BROADCASTS. Until that transaction confirms, this call is
    rejected with "current task status is Init; setting the payment mode is
    only allowed in `created` status".

    Polling `agent status` first is NOT sufficient, which cost six tasks in the
    2026-08-04 run: status reported `created` in 10-14s while set-payment-mode
    still saw Init. The two endpoints do not share a read-your-writes view, so
    the only reliable fix is retrying THIS call until it takes.
    """
    last = ""
    for attempt in range(attempts):
        rc, out, err = run([
            "agent", "set-payment-mode", job,
            "--payment-mode", "x402", "--chain", CHAIN,
            "--token-symbol", TOKEN_SYMBOL, "--token-amount", TOKEN_AMOUNT,
            "--endpoint", ENDPOINT,
        ])
        if rc == 0:
            return True, out
        last = out + err
        settling = "status is Init" in last or "only allowed in `created`" in last
        if not settling:
            return False, last
        time.sleep(backoff * (attempt + 1))
    return False, f"still settling after {attempts} attempts: {last[:300]}"


def pay(job: str, i: int, accepts: str):
    rc, out, err = run([
        "agent", "task-402-pay", job,
        "--provider-agent-id", PROVIDER, "--accepts", accepts,
        "--endpoint", ENDPOINT, "--chain", CHAIN,
        "--token-symbol", TOKEN_SYMBOL, "--token-amount", TOKEN_AMOUNT,
        "--body", json.dumps(body_for(i)),
    ], timeout=300)
    stored = '"stored":true' in (out or "").replace(" ", "")
    return rc == 0, stored, (out + err)


def purchase(i: int, accepts: str):
    job, out = create_task(i)
    if not job:
        return False, None, f"create-task failed: {out[:300]}"
    ok, note = set_payment_mode(job)
    if not ok:
        return False, job, f"set-payment-mode failed: {note[:300]}"
    ok, stored, note = pay(job, i, accepts)
    if not ok:
        return False, job, f"task-402-pay failed: {note[:400]}"
    mark_paid(job)
    return True, job, "stored" if stored else "paid, but the endpoint did not confirm a write"


# Ledger of job ids this script has paid for. Gitignored: it is local bookkeeping,
# not shared state.
LEDGER = REPO / "scripts" / ".okx-paid.jsonl"


def mark_paid(job: str) -> None:
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"job": job, "at": int(time.time())}) + "\n")


def already_paid() -> set[str]:
    if not LEDGER.exists():
        return set()
    out = set()
    for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.add(json.loads(line)["job"])
        except Exception:
            continue
    return out


def unpaid_tasks():
    """Job ids that were created but that this script never recorded paying.

    TASK STATUS CANNOT TELL YOU THIS. A paid task stays at `created`; payment
    does not move it to `accepted`. Filtering on status == "created" therefore
    selects EVERY task ever bought, and re-paying them is silent duplicate
    spending. That mistake cost 0.10 USDT0 and 10 duplicate sales on
    2026-08-04 before it was caught.

    So the only trustworthy signal is our own ledger of what we paid. Anything
    created but absent from the ledger is a genuine candidate, and even then the
    caller must pass --yes, because a missing ledger entry could just mean the
    ledger was cleared.
    """
    paid = already_paid()
    rc, out, err = run(["agent", "active-tasks"])
    jobs = []
    for m in re.finditer(r'"jobId":"(0x[0-9a-f]{64})"[^}]*?"status":"(\w+)"', out):
        if m.group(2) == "created" and m.group(1) not in paid:
            jobs.append(m.group(1))
    return jobs


# --- entry ------------------------------------------------------------------

def main() -> None:
    load_env()
    argv = sys.argv[1:]

    if "--pay-only" in argv:
        job = argv[argv.index("--pay-only") + 1]
        accepts = accepts_array()
        ok, note = set_payment_mode(job)
        print(f"set-payment-mode: {'ok' if ok else note}")
        ok, stored, note = pay(job, 1, accepts)
        print(f"pay: {'ok' if ok else note}  stored={stored}")
        return

    if "--recover" in argv:
        jobs = unpaid_tasks()
        print(f"{len(jobs)} task(s) created but not in the paid ledger:")
        for job in jobs:
            print(f"  {job}")
        if not jobs:
            return
        # Dry by default. Paying is irreversible and a missing ledger entry is
        # not proof a task is unpaid, so the operator has to say so explicitly.
        if "--yes" not in argv:
            print("\nDry run. Re-run with --yes to pay these. Verify first: a task\n"
                  "already paid also shows `created`, so an empty or cleared ledger\n"
                  "makes every past purchase look unpaid.")
            return
        accepts = accepts_array()
        for n, job in enumerate(jobs, 1):
            ok, note = set_payment_mode(job)
            paid, stored, _ = pay(job, n, accepts) if ok else (False, False, "")
            if paid:
                mark_paid(job)
            print(f"[{n}] {job[:12]}... {'OK' if paid else 'FAIL'} stored={stored}", flush=True)
        return

    count = int(argv[0]) if argv and argv[0].isdigit() else 1
    start = int(argv[argv.index("--start-index") + 1]) if "--start-index" in argv else 1

    accepts = accepts_array()
    print(f"identity {USER_KEY}, folder {FOLDER}")
    print(f"buyer USDT0 before: {usdt_balance()}", flush=True)

    ok = fails = 0
    t0 = time.time()
    for n in range(start, start + count):
        began = time.time()
        good, job, note = purchase(n, accepts)
        took = round(time.time() - began, 1)
        if good:
            ok += 1
            fails = 0
            print(f"[{n}] OK {took}s {note} (ok={ok})", flush=True)
        else:
            fails += 1
            print(f"[{n}] FAIL {took}s job={job} :: {note}", flush=True)
            # Every create-task broadcasts before payment, so grinding through
            # failures orphans a task each time. Stop and let --recover clean up.
            if fails >= 3:
                print("three consecutive failures, stopping. Run --recover to pay any created tasks.")
                break

    print(f"\n{ok}/{count} purchased in {round(time.time()-t0)}s")
    print(f"buyer USDT0 after: {usdt_balance()}")


if __name__ == "__main__":
    main()
