# Kontext VPS Deployment Runbook

Deploys the Kontext cloud control plane (sync server + dream/digest worker)
to a Hetzner (or any Docker-capable) VPS, behind Caddy auto-TLS.

## Inputs

- Local package directory: `deploy/remote/kontext`
- Local env file: `deploy/remote/kontext/.env`
- Server target directory: `/opt/kontext`
- Public domain: `https://kontext.ionutrosu.xyz`
- VPS OS: Ubuntu 24.04 LTS or similar Docker-capable Ubuntu release

## 1. Build and publish the image (one-time + on every code change)

From the kontext repo root:

```bash
docker build -t ghcr.io/ionutrosu/kontext:latest .
docker login ghcr.io -u ionutrosu
docker push ghcr.io/ionutrosu/kontext:latest
```

Expected:
- `docker images | grep kontext` shows the tagged image
- The push completes without auth errors

If you do not want to use GHCR, build on the VPS directly:
```bash
scp -r . root@YOUR_SERVER_IP:/opt/kontext-src
ssh root@YOUR_SERVER_IP "cd /opt/kontext-src && docker build -t kontext:latest ."
```
Then set `KONTEXT_IMAGE=kontext:latest` in `.env` instead of the GHCR tag.

## 2. Fill the env file locally

```powershell
Copy-Item deploy/remote/kontext/.env.example deploy/remote/kontext/.env -Force
notepad deploy/remote/kontext/.env
```

Required edits:
- set `KONTEXT_DOMAIN` to your real public host name (default: `kontext.ionutrosu.xyz`)
- set `ACME_EMAIL` to your certificate email (Let's Encrypt will notify on cert issues)
- confirm `KONTEXT_IMAGE` points to an image the VPS can pull
- leave `DREAM_INTERVAL_HOURS` / `DIGEST_INTERVAL_HOURS` as defaults unless you have
  a reason to change them. `0` disables that loop.

## 3. Install Docker on the Ubuntu VPS

```bash
ssh root@YOUR_SERVER_IP "apt-get update && apt-get install -y ca-certificates curl gnupg && install -m 0755 -d /etc/apt/keyrings && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && chmod a+r /etc/apt/keyrings/docker.asc && echo 'deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable' > /etc/apt/sources.list.d/docker.list && apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
```

Expected:
- `docker --version` works on the server
- `docker compose version` works on the server

Skip this step if Docker is already installed (it probably is, since you already run
other services on this box).

## 4. Copy the package to the server

```bash
ssh root@YOUR_SERVER_IP "mkdir -p /opt/kontext"
scp deploy/remote/kontext/.env root@YOUR_SERVER_IP:/opt/kontext/.env
scp deploy/remote/kontext/docker-compose.yml root@YOUR_SERVER_IP:/opt/kontext/docker-compose.yml
scp deploy/remote/kontext/Caddyfile root@YOUR_SERVER_IP:/opt/kontext/Caddyfile
```

Expected:
- `/opt/kontext/.env` exists on the server
- `/opt/kontext/docker-compose.yml` exists on the server
- `/opt/kontext/Caddyfile` exists on the server

## 5. Point DNS to the VPS

Create or update an `A` record for your Kontext host name so it points to the
VPS public IP. For `kontext.ionutrosu.xyz`, add the record in whatever DNS
provider holds `ionutrosu.xyz`.

Expected:
- `dig +short kontext.ionutrosu.xyz` returns your VPS IP before starting Caddy
- Caddy will fail the ACME challenge if the domain does not resolve yet

## 6. Start the Kontext stack

```bash
ssh root@YOUR_SERVER_IP "cd /opt/kontext && mkdir -p data/kontext data/caddy_data data/caddy_config && docker compose pull && docker compose up -d"
```

Expected:
- `kontext`, `kontext-worker`, and `kontext-caddy` containers are running
- `docker compose ps` shows all three as `Up`

## 7. Verify the live endpoint

```bash
ssh root@YOUR_SERVER_IP "cd /opt/kontext && docker compose ps"
ssh root@YOUR_SERVER_IP "cd /opt/kontext && docker compose logs --tail 30 kontext"
curl -I https://kontext.ionutrosu.xyz/docs
```

Expected:
- `curl -I` returns `HTTP/2 200` with a valid TLS cert
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
    server_url='https://kontext.ionutrosu.xyz',
    workspace_id='ws-ionut',
    label='Desktop',
    workspace_name='Ionut primary',
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
    server_url='https://kontext.ionutrosu.xyz',
    workspace_id='ws-ionut',
    label='Laptop',
    workspace_token='<paste the token from step 8>',
)
print(status)
"
```

Limits (enforced by the server):
- Max 2 `interactive` devices (your PC + laptop)
- Max 1 `server` device (reserved for things like Codex running on the VPS itself)

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

## 12. Rotate / redeploy

On code changes:
1. Rebuild and push a new image tag (step 1).
2. On the VPS: `cd /opt/kontext && docker compose pull && docker compose up -d`.
3. Check `docker compose ps` — all three containers should come back `Up`.

## 13. Known caveats

- **Pulling the image on first boot takes a few minutes** because sentence-transformers
  ships with PyTorch. Subsequent pulls are diffs.
- **SQLite is single-writer.** The worker and API both hold write connections, but
  SQLite serializes writes at the file level, so short contention spikes are normal
  while a dream cycle is running.
- **Losing the `workspace_token` means losing access to your brain.** Put it in your
  password manager. If lost, you must manually reset `workspaces.api_token_hash` on
  the VPS and re-link every device.
