#!/usr/bin/env bash
set -euo pipefail

REPO_HOST="3.26.237.76"
SSH_KEY="${HOME}/.ssh/vm-key.pem"
SSH_USER="ubuntu"

if [[ ! -f "${SSH_KEY}" ]]; then
  echo "Missing SSH key: ${SSH_KEY}" >&2
  exit 1
fi

ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=accept-new "${SSH_USER}@${REPO_HOST}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/Repona
git pull origin master
docker compose -f docker-compose.hosted.yml up -d --build
health_ok=false
for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/api/v1/health; then
    echo
    health_ok=true
    break
  fi
  sleep 3
done
if [[ "${health_ok}" != true ]]; then
  echo "Health check failed on VM after 60s" >&2
  exit 1
fi
docker exec repona-api env PYTHONPATH=/app/backend python /app/scripts/repair_catalog.py --all --reset-expired-refresh
echo "HEAD: $(git rev-parse --short HEAD) $(git log -1 --format=%s)"
REMOTE

echo "Public health:"
curl -fsS "http://${REPO_HOST}:8000/api/v1/health"
echo
