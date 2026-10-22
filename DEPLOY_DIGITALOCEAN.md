# Deploying Easyclip on a DigitalOcean Droplet

Easyclip runs as seven Docker containers (frontend, backend, worker, Postgres,
Redis, the YouTube token helper and MCP). A Droplet runs them with Docker
Compose, and Caddy in front gives both public addresses HTTPS.

You need:

- A domain, for example `example.com`, with two names pointing at the Droplet:
  `app.example.com` (the website) and `api.example.com` (the backend the browser calls).
- Your API keys: AssemblyAI, Anthropic (or another LLM), optional Freesound.

## 1. Create the Droplet

- Image: **Marketplace → Docker on Ubuntu 24.04**
- Size: **Basic, 4 vCPU / 8 GB** at least (CPU-Optimized encodes faster)
- Region: closest to you (London, Frankfurt, Amsterdam)
- Authentication: **SSH key** (safer than a password)

Note the Droplet's IP address.

## 2. Point the domain at it

At your domain registrar, add two **A records** with the Droplet's IP:

| Name  | Type | Value         |
|-------|------|---------------|
| `app` | A    | `DROPLET_IP`  |
| `api` | A    | `DROPLET_IP`  |

## 3. Install Easyclip

Connect with `ssh root@DROPLET_IP`, then:

```bash
apt-get update && apt-get install -y git caddy fonts-dejavu fonts-liberation
git clone https://github.com/abahvictor360-sketch/Easyclip.git /opt/easyclip
cd /opt/easyclip
cp .env.example .env
```

## 4. Configure `.env`

Edit with `nano .env`. Replace `example.com` with your domain:

```bash
# Production frontend build
FRONTEND_BUILD_TARGET=runner
NODE_ENV=production
FRONTEND_PORT=3107

# Public addresses
NEXT_PUBLIC_APP_URL=https://app.example.com
BETTER_AUTH_URL=https://app.example.com
NEXT_PUBLIC_API_URL=https://api.example.com
CORS_ORIGINS=https://app.example.com

# Keys
ASSEMBLY_AI_API_KEY=...
ANTHROPIC_API_KEY=...
LLM=anthropic:claude-sonnet-5-5
FREESOUND_API_KEY=...
```

Then replace every placeholder secret with a fresh random value:

```bash
for k in BETTER_AUTH_SECRET BACKEND_AUTH_SECRET APP_SETTINGS_ENCRYPTION_KEY; do
  sed -i "s|^$k=.*|$k=$(openssl rand -hex 32)|" .env
done
```

The database password is set inside `docker-compose.yml`. Postgres is only
reachable from the other containers, never from the internet, so it can stay.

Optional: set `DISABLE_SIGN_UP=true` after you create your own account so
strangers cannot sign up.

## 5. HTTPS with Caddy

Replace `/etc/caddy/Caddyfile` with:

```
app.example.com {
    reverse_proxy 127.0.0.1:3107
}

api.example.com {
    reverse_proxy 127.0.0.1:8001
}
```

```bash
systemctl reload caddy
```

Caddy gets the HTTPS certificates automatically once the DNS records from step 2 resolve.

## 6. Start it

```bash
docker compose up -d --build
docker compose ps
```

The first build takes 10-20 minutes. Then open `https://app.example.com` and sign up.

## 7. Firewall

Only SSH and the web ports should be open:

```bash
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

All containers listen on `127.0.0.1` only, so Postgres, Redis and the backend
port are not reachable from the internet directly.

## Day-to-day

| Task | Command (in `/opt/easyclip`) |
|------|------------------------------|
| Update to the latest code | `git pull && docker compose up -d --build` |
| Watch video processing | `docker compose logs -f worker` |
| Restart everything | `docker compose restart` |
| Disk use | `docker system df` |

## Server-specific notes

- **YouTube blocks data-centre IPs more often.** If imports fail with
  "Sign in to confirm you're not a bot", copy a `youtube.txt` cookies file into
  `/opt/easyclip/cookies/`. Uploading video files always works.
- **Fonts:** fonts in `/usr/share/fonts` and in `/opt/easyclip/fonts-local/` appear
  in the font picker.
- **Music:** put your own tracks in `/opt/easyclip/audio/music` and effects in
  `/opt/easyclip/audio/sfx`.
- **Backups:** enable DigitalOcean's weekly Droplet backups, or dump the database
  with `docker compose exec postgres pg_dump -U supoclip supoclip > backup.sql`.
