# Kontext VPS Deployment Runbook

Deploys the Kontext cloud control plane (sync server + dream/digest worker)
to any Docker-capable VPS, behind Caddy auto-TLS.

## Inputs

- Local package directory: `deploy/remote/kontext`
- Local env file: `deploy/remote/kontext/.env` (created in step 2)
- Server target directory: `/opt/kontext`
- Public domain: `https://YOUR_DOMAIN` (set by you in `.env`)
- VPS OS: Ubuntu 24.04 LTS or similar Docker-capable Ubuntu release

> **Dashboard is localhost-only.** The public domain serves only the sync
> API. To see the dashboard, SSH-tunnel port 8200 and open
> `http://localhost:8200/dashboard` on your own machine. Detail in step 12.

## 1. Build the image on the VPS (simplest path)

The quickest route is to build on the VPS itself — no GHCR login required.

```bash
ssh root@YOUR_SERVER_IP "mkdir -p /opt/kontext/src"
scp -r . root@YOUR_SERVER_IP:/opt/kontext/src
ssh root@YOUR_SERVER_IP "cd /opt/kontext/src && docker build -t kontext:latest ."
```

Expected:
- `docker images | grep kontext` on the VPS shows `kontext:latest`
- `KONTEXT_IMAGE=kontext:latest` in `.env` (this is the default)

Alternative (publish to GHCR instead):
```bash
docker build -t ghcr.io/YOUR_USERNAME/kontext:latest .
docker login ghcr.io -u YOUR_USERNAME
docker push ghcr.io/YOUR_USERNAME/kontext:latest
```
Then set `KONTEXT_IMAGE=ghcr.io/YOUR_USERNAME/kontext:latest` in `.env`.

## 2. Fill the env file locally

```bash
cp deploy/remote/kontext/.env.example deploy/remote/kontext/.env
$EDITOR deploy/remote/kontext/.env
```

Required edits:
- set `KONTEXT_DOMAIN` to your real public host name (e.g. `kontext.example.com`)
- set `ACME_EMAIL` to your certificate email (Let's Encrypt will notify on cert issues)
- confirm `KONTEXT_IMAGE` matches whatever you built/pushed in step 1
- leave `DREAM_INTERVAL_HOURS` / `DIGEST_INTERVAL_HOURS` as defaults unless you have
  a reason to change them. `0` disables that loop.

## 3. Install Docker on the Ubuntu VPS

```bash
ssh root@YOUR_SERVER_IP "apt-get update && apt-get install -y ca-certificates curl gnupg && install -m 0755 -d /etc/apt/keyrings && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && chmod a+r /etc/apt/keyrings/docker.asc && echo 'deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable' > /etc/apt/sources.list.d/docker.list && apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
```

Expected:
- `docker --version` works on the server
- `docker compose version` works on the server

Skip this step if Docker is already installed.

## 4. Copy the deploy package to the server

```bash
scp deploy/remote/kontext/.env root@YOUR_SERVER_IP:/opt/kontext/.env
scp deploy/remote/kontext/docker-compose.yml root@YOUR_SERVER_IP:/opt/kontext/docker-compose.yml
scp deploy/remote/kontext/Caddyfile root@YOUR_SERVER_IP:/opt/kontext/Caddyfile
```

Expected:
- `/opt/kontext/.env`, `/opt/kontext/docker-compose.yml`, `/opt/kontext/Caddyfile` exist

## 5. Point DNS to the VPS

Create or update an `A` record for your Kontext host name so it points to the
VPS public IP.

Expected:
- `dig +short YOUR_DOMAIN` returns your VPS IP before starting Caddy
- Caddy will fail the ACME challenge if the domain does not resolve yet

## 6. Start the Kontext stack

```bash
ssh root@YOUR_SERVER_IP "cd /opt/kontext && mkdir -p data/kontext data/caddy_data data/caddy_config && docker compose up -d"
```

Expected:
- `kontext`, `kontext-worker`, and `kontext-caddy` containers are running
- `docker compose ps` shows all three as `Up`

## 7. Verify the live endpoint

```bash
ssh root@YOUR_SERVER_IP "cd /opt/kontext && docker compose ps"
ssh root@YOUR_SERVER_IP "cd /opt/kontext && docker compose logs --tail 30 kontext"
curl -I https://YOUR_DOMAIN/docs
```

Expected:
- `curl -I` returns `HTTP/2 200` with a valid TLS cert
- `curl -I https://YOUR_DOMAIN/dashboard` returns **403** — dashboard is
  localhost-only, Caddy refuses to proxy it
- Container logs show `[kontext.server] db=/app/data/kontext.db bind=0.0.0.0:8080`
- No repeated ACME errors in `kontext-caddy` logs

## 8. Link your first device (this PC)

From your local machine, with the kontext repo checked out:

```bash
python -c "
from db import KontextDB
from cloud.daemon import link_workspace
db = KontextDB('kontext.db')
status = link_workspace(
    db,
    server_url='https://YOUR_DOMAIN',
    workspace_id='ws-YOURNAME',
    label='Desktop',
    workspace_name='YOUR_NAME primary',
    recovery_key_id='recovery-1',
)
print(status)
"
```

Expected:
- The output contains `workspace_token` — **save this somewhere safe** (password manager).
  It is the bearer token that authorizes every device and the dashboard.
- The output contains `device_id` — this device is now enrolled as `interactive`.

## 9. Link additional devices

On the second device, pass the workspace_token from step 8:

```bash
python -c "
from db import KontextDB
from cloud.daemon import link_workspace
db = KontextDB('kontext.db')
status = link_workspace(
    db,
    server_url='https://YOUR_DOMAIN',
    workspace_id='ws-YOURNAME',
    label='Laptop',
    workspace_token='<paste the token from step 8>',
)
print(status)
"
```

Limits (enforced by the server):
- Max 2 `interactive` devices (your PC + laptop)
- Max 1 `server` device (reserved for automated jobs)

## 10. Verify sync works end-to-end

On device A, write a memory op. On device B, pull and confirm it arrived:

```bash
# on A
python -c "from cloud.daemon import sync_once; from db import KontextDB; print(sync_once(KontextDB('kontext.db')))"

# on B
python -c "from cloud.daemon import sync_once; from db import KontextDB; print(sync_once(KontextDB('kontext.db')))"
```

Expected:
- Both calls return without raising. The op count pulled on device B should
  include at least one new row matching what device A pushed.

## 11. Verify the worker is running dream + digest

```bash
ssh root@YOUR_SERVER_IP "cd /opt/kontext && docker compose logs --tail 50 kontext-worker"
```

Expected (within ~30 seconds of `up -d`, depending on `WORKER_STARTUP_DELAY_SEC`):
- `[kontext.worker ...] starting db=/app/data/kontext.db dream_interval=24.0h digest_interval=6.0h`
- After the configured interval: `dream start`, `dream done`, `digest start`, `digest done`

## 12. Reach the dashboard (localhost-only)

The dashboard is bound to `127.0.0.1:8200` on the VPS and blocked at the
public domain. To open it:

```bash
# on your laptop / workstation
ssh -L 8200:localhost:8200 root@YOUR_SERVER_IP
```

Leave that SSH session open, then browse to:

```
http://localhost:8200/dashboard
```

Close the SSH session to "log out" of the dashboard — nothing is exposed
publicly, no auth gate is needed.

## 13. Rotate / redeploy

On code changes:
1. Rebuild the image on the VPS (`docker build -t kontext:latest .` inside
   `/opt/kontext/src`) or push a new tag to your registry.
2. `cd /opt/kontext && docker compose up -d --force-recreate kontext kontext-worker`
3. Check `docker compose ps` — all three containers should come back `Up`.

## 14. Known caveats

- **First build takes a few minutes** because sentence-transformers ships with
  PyTorch. Subsequent rebuilds are layer-diffs.
- **SQLite is single-writer.** The worker and API both hold write connections, but
  SQLite serializes writes at the file level, so short contention spikes are normal
  while a dream cycle is running.
- **Losing the `workspace_token` means losing access to your brain.** Put it in your
  password manager. If lost, you must manually reset `workspaces.api_token_hash` on
  the VPS and re-link every device.
