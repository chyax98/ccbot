---
name: deploy
description: Deploy applications to cloud platforms — Fly.io, Railway, Vercel, or via SSH. Use when the user wants to publish, deploy, or release an application.
metadata: {"ccbot":{"emoji":"🚀","requires":{"bins":["curl"]}}}
---

# Deploy Skill

## Fly.io

```bash
# Install CLI (if needed)
curl -L https://fly.io/install.sh | sh

# First deploy (creates fly.toml)
fly launch --name myapp --region sin

# Redeploy after changes
fly deploy

# Scale
fly scale count 2          # 2 instances
fly scale memory 512       # 512MB RAM

# Manage secrets
fly secrets set DATABASE_URL="postgres://..." API_KEY="..."
fly secrets list

# Logs & status
fly logs -a myapp
fly status -a myapp
fly ssh console -a myapp   # SSH into instance
```

## Railway

```bash
# Install CLI
npm install -g @railway/cli

# Login & deploy
railway login
railway link           # link to existing project
railway up             # deploy

# Environment variables
railway variables set KEY=value
railway variables      # list

# Logs
railway logs
```

## Vercel (frontend / Next.js)

```bash
# Install
npm install -g vercel

# Deploy
vercel                  # interactive
vercel --prod           # production deploy

# Environment variables
vercel env add NEXT_PUBLIC_API_URL production
vercel env ls

# Domains
vercel domains add myapp.com
```

## SSH / VPS Deploy

```bash
# Basic rsync deploy
rsync -avz --exclude='.git' --exclude='node_modules' \
  ./ user@server.example.com:/var/www/myapp/

# Deploy via SSH with restart
ssh user@server.example.com "
  cd /var/www/myapp
  git pull origin main
  uv sync --no-dev
  systemctl restart myapp
  echo 'Deploy complete'
"

# Check service status
ssh user@server.example.com "systemctl status myapp"
```

## Docker to Remote Server

```bash
# Build locally, push to registry, pull on server
docker buildx build --platform linux/amd64 \
  -t ghcr.io/owner/myapp:$(git rev-parse --short HEAD) --push .

TAG=$(git rev-parse --short HEAD)
ssh user@server.example.com "
  docker pull ghcr.io/owner/myapp:$TAG
  docker stop myapp || true
  docker rm myapp || true
  docker run -d --name myapp \
    -p 8080:8080 \
    --env-file /etc/myapp.env \
    --restart unless-stopped \
    ghcr.io/owner/myapp:$TAG
  echo 'Running: $TAG'
"
```

## GitHub Actions Deploy (CI-triggered)

Check current workflow status:

```bash
gh run list --repo owner/repo --limit 5
gh run view $(gh run list --repo owner/repo --limit 1 --json databaseId -q '.[0].databaseId')
```

Trigger manual deploy:

```bash
gh workflow run deploy.yml --repo owner/repo \
  -f environment=production \
  -f version=v1.2.0
```

## Health Check After Deploy

```bash
# Wait for service to be healthy
URL="https://myapp.fly.dev/health"
for i in $(seq 1 12); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$URL")
  if [ "$STATUS" = "200" ]; then
    echo "✅ Deploy successful — $URL is healthy"
    break
  fi
  echo "Waiting ($i/12)... HTTP $STATUS"
  sleep 5
done
[ "$STATUS" != "200" ] && echo "❌ Health check failed after 60s"
```

## Tips

- Always tag Docker images with git commit SHA (not just `latest`).
- Run health checks after every deploy to confirm success.
- Use `fly secrets` / `railway variables` for secrets — never commit `.env`.
- For zero-downtime: Fly.io and Railway handle it natively; for SSH deploys use blue-green.
