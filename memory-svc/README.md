# Memory sidecar

A small Node service that keeps each user's memory encrypted on Walrus, organised as user, then folders, then notes. The app calls it internally; it is never exposed to the internet.

Every note is encrypted with AES-256-GCM under a key derived from the user's passphrase (scrypt, with a stable per-user salt) before it leaves this process, so what reaches Walrus is ciphertext. On recall, the folder's items are pulled, decrypted transiently with the passphrase supplied on that request, ranked against the query, and returned. The passphrase and the plaintext are never stored.

Identity and folder names are case-insensitive. Writes reply `ok: true` only after the relayer confirms the note reached Walrus, and carry the blob id as a receipt; a failed write reports the reason instead of pretending.

## Why a separate service

The MemWal client is TypeScript on the Sui and Walrus stack, and the app is Python. Rather than couple them in one process, this sidecar exposes the memory operations over plain HTTP on the private network, and the Python app calls them.

## API

- `GET /health` returns `{ status, enabled }`. `enabled` is false when no MemWal credentials are set, in which case the service runs but stores nothing.
- `POST /remember` with `{ user, passphrase, text, folder }` writes one encrypted note. Returns `{ ok, enabled, blob_id }` on a confirmed write, or `{ ok: false, error }` when the relayer did not confirm.
- `POST /recall` with `{ user, passphrase, query, folder, limit }` returns `{ enabled, records, scanned, total, truncated, locked }`. `truncated` means the folder holds more than could be scanned. `locked` means notes exist but this passphrase opens none of them: a wrong passphrase, not an empty memory.
- `POST /forget` with `{ user, passphrase, folder }` retires the folder: its notes are never served again and it starts fresh. Honoured only when the passphrase decrypts at least one existing note (`403` otherwise), so an identity string alone cannot wipe anything.

## How forget works

Notes carry no timestamps, so a folder is forgotten by generation: a plaintext `gen:N` marker in a control namespace moves the folder's data namespace, and the service never reads the old one again. The old ciphertext stays on Walrus until its storage expires, sealed under the passphrase. Revocation, not erasure; key destruction is the only true delete on immutable storage.

## Configuration

- `MEMWAL_PRIVATE_KEY`, `MEMWAL_ACCOUNT_ID` from [memory.walrus.xyz](https://memory.walrus.xyz). Without them the service degrades to a no-op.
- `MEMWAL_SERVER_URL` (default `https://relayer.memwal.ai`).
- `AGENT_MEMORY_FETCH_LIMIT` (default 100) and `AGENT_MEMORY_FETCH_MAX` (default 500): the first pull per folder, and the ceiling when the relayer reports more.
- `AGENT_MEMORY_REMEMBER_TIMEOUT_MS` (default 45000): how long to wait for write confirmation before reporting failure.
- `AGENT_MEMORY_RETIRED_USERS`: comma-separated identities this service refuses to serve, for retiring a compromised or abandoned identity.
- `MEMORY_SVC_PORT` (default 4000), `MEMORY_SVC_HOST` (default 0.0.0.0).

## Run locally

```
cd memory-svc
npm install
MEMWAL_PRIVATE_KEY=... MEMWAL_ACCOUNT_ID=... npm start
curl -s http://127.0.0.1:4000/health
```
