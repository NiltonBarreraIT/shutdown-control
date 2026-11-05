#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/shutdown-control}"
BRANCH="${DEPLOY_BRANCH:-main}"
TARGET_SHA="${1:-}"

cd "$APP_DIR"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: la VPS tiene cambios locales sin registrar; despliegue cancelado." >&2
  exit 1
fi

git fetch --prune origin "$BRANCH"

if [[ -n "$TARGET_SHA" ]]; then
  git merge-base --is-ancestor "$TARGET_SHA" "origin/$BRANCH" || {
    echo "ERROR: el commit solicitado no pertenece a origin/$BRANCH." >&2
    exit 1
  }
  git checkout --detach "$TARGET_SHA"
else
  git checkout "$BRANCH"
  git merge --ff-only "origin/$BRANCH"
fi

docker compose config --quiet
docker compose up -d --build --remove-orphans

DEPLOYED_SHA="$(git rev-parse HEAD)"
printf '%s commit=%s branch=%s\n' "$(date --iso-8601=seconds)" "$DEPLOYED_SHA" "$BRANCH" | tee -a deploy-history.log
docker compose ps

