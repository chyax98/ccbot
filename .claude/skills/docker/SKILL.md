---
name: docker
description: Build, run, and manage Docker containers and images. Use when the user asks about containers, Docker builds, logs, or deployment.
metadata: {"ccbot":{"emoji":"🐳","requires":{"bins":["docker"]}}}
---

# Docker Skill

Use `docker` and `docker compose` via the `Bash` tool.

## Build Image

```bash
# Standard build
docker build -t myapp:latest .

# Build with args, specific Dockerfile
docker build -t myapp:v1.2.0 \
  --build-arg NODE_ENV=production \
  -f deploy/Dockerfile \
  .

# Multi-platform build
docker buildx build --platform linux/amd64,linux/arm64 \
  -t ghcr.io/owner/myapp:latest --push .
```

## Run Container

```bash
# Foreground
docker run --rm -it myapp:latest

# Background daemon
docker run -d \
  --name myapp \
  -p 8080:8080 \
  -v "$(pwd)/data:/data" \
  -e DATABASE_URL="$DATABASE_URL" \
  --restart unless-stopped \
  myapp:latest
```

## Container Management

```bash
# List
docker ps                     # running
docker ps -a                  # all including stopped

# Logs
docker logs myapp -f          # follow
docker logs myapp --tail 100  # last 100 lines

# Execute inside container
docker exec -it myapp bash
docker exec myapp cat /etc/hosts

# Stop / Remove
docker stop myapp
docker rm myapp
docker rm -f myapp            # force stop + remove
```

## Image Management

```bash
docker images                             # list local images
docker pull ghcr.io/owner/app:latest      # pull
docker rmi old-image:v1.0                 # remove
docker tag myapp:latest myapp:v1.2.0      # tag
docker push ghcr.io/owner/app:latest      # push to registry
```

## Docker Compose

```bash
docker compose up -d                       # start all services
docker compose down                        # stop + remove containers
docker compose down -v                     # also remove volumes
docker compose logs -f web                 # follow service logs
docker compose exec web bash               # shell into service
docker compose ps                          # status
docker compose restart web                 # restart one service
```

## System Cleanup

```bash
docker system prune -f           # remove unused containers, networks, images
docker system prune -af          # also remove unused images (aggressive)
docker volume prune -f           # remove unused volumes
docker system df                 # disk usage breakdown
```

## Inspect & Debug

```bash
# Container details
docker inspect myapp | python3 -m json.tool

# Resource usage
docker stats --no-stream

# Port mappings
docker port myapp

# Network
docker network ls
docker network inspect bridge
```

## Registry Login

```bash
# GitHub Container Registry
echo "$GITHUB_TOKEN" | docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin

# Docker Hub
docker login -u "$DOCKERHUB_USER" -p "$DOCKERHUB_TOKEN"
```

## Tips

- Use `--rm` for one-off containers that should auto-clean up.
- Always pin image versions in production (`myapp:v1.2.0` not `latest`).
- Use `docker compose` for multi-container setups.
- Check resource usage with `docker stats` before running heavy builds.
