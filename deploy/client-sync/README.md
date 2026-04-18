# Kontext Client Sync

Pulls the Kontext memory library from the central cloud server into a client
machine (PC, remote server, VPS container) on a 2-minute cron tick, and
re-exports every entry as a `.md` file so the routing hooks in
`../../routing/` can read fresh content.

Designed for setups where a single workspace is shared across multiple
devices — e.g. Claude on your laptop and Claude inside a remote `ttyd-claude`
container should both see the same memory within ~2 min.

## What it does, each tick

1. **Self-seed push cursor** on the very first run (prevents re-uploading
   6k+ pulled ops — `history_ops.id` is a UUID, not insertion-ordered, and
   one op from a revoked co-tenant device is enough to 403 the entire
   batch push).
2. **Scoped push** — only ops authored by THIS device are pushed back up.
   Ops pulled from other devices stay where they are.
3. **Pull** — `cloud.daemon._pull_history` + `_pull_canonical`.
4. **Export** — `export.export_all` regenerates every `.md` file from the
   live SQLite state. mtime changes on those files invalidate the router's
   session state so the next Claude session loads fresh content.

Typical tick when idle: ~300 ms, zero network transfer.

## Prereqs

- `/root/kontext-mcp/` contains the Kontext codebase (same repo as the
  cloud server — the client imports `db.py`, `export.py`, `cloud/daemon.py`).
- Device is enrolled against the cloud server. After enrollment you should
  have `/root/.kontext/server.cloud.json` and `/root/.kontext/keys/*`.
- Python 3.12+ with the deps from `requirements.txt` installed.

## Install

### On a bare Linux host

```bash
cp _sync.sh /root/.kontext/_sync.sh
chmod +x /root/.kontext/_sync.sh
(crontab -l 2>/dev/null; echo '*/2 * * * * /root/.kontext/_sync.sh >> /root/.kontext/_sync.log 2>&1') | crontab -
```

### On a VPS host targeting a bind-mounted container (e.g. `ttyd-claude`)

```bash
# Place the script where the container's /root/ is bind-mounted.
cp _sync.sh /root/.kontext/_sync.sh
chmod +x /root/.kontext/_sync.sh
# Cron runs on the host and exec's into the container:
(crontab -l 2>/dev/null; echo '*/2 * * * * docker exec <container_name> bash /root/.kontext/_sync.sh >> /root/.kontext/_sync.log 2>&1') | crontab -
```

## Verify

```bash
bash /root/.kontext/_sync.sh   # run once manually, expect JSON output
tail -n3 /root/.kontext/_sync.log
```

Each line is one JSON object: `{ts, elapsed_ms, pushed, pulled_history, pulled_canonical, md_files}`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `device is revoked` 403 on push | Some pulled op's device_id was revoked on the cloud side | Already handled by scoped push. If persisting, confirm `state['device_id']` matches a non-revoked device in the cloud DB. |
| `pulled_history > 0` every tick, never drops to 0 | New ops arriving faster than cron tick | Drop cron to `* * * * *` (1 min) or investigate write rate. |
| `md_files` drops to 0 | `export_all` failure or memory_root permission | Check `_sync.log` stderr, `ls -la /root/.kontext/memory/` for ownership. |

## Pairs with

- `../../routing/` — Claude Code hooks that auto-load the `.md` files this
  script produces. Install both on the client for the full setup.
