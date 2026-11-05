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
BUILD_ARGS=(--remove-orphans)

if [[ -n "$TARGET_SHA" ]]; then
  git merge-base --is-ancestor "$TARGET_SHA" "origin/$BRANCH" || {
    echo "ERROR: el commit solicitado no pertenece a origin/$BRANCH." >&2
    exit 1
  }
  if [[ "$(git rev-parse HEAD)" == "$TARGET_SHA" ]]; then
    echo "Codigo sin cambios: verificando que el servicio este iniciado."
  else
    git checkout --detach "$TARGET_SHA"
    BUILD_ARGS=(--build --remove-orphans)
  fi
else
  CURRENT_SHA="$(git rev-parse HEAD)"
  REMOTE_SHA="$(git rev-parse "origin/$BRANCH")"
  if [[ "$CURRENT_SHA" == "$REMOTE_SHA" ]]; then
    echo "Codigo sin cambios: verificando que el servicio este iniciado."
  else
    git checkout "$BRANCH"
    git merge --ff-only "origin/$BRANCH"
    BUILD_ARGS=(--build --remove-orphans)
  fi
fi

docker compose config --quiet
docker compose up -d "${BUILD_ARGS[@]}"

DEPLOYED_SHA="$(git rev-parse HEAD)"
printf '%s commit=%s branch=%s\n' "$(date --iso-8601=seconds)" "$DEPLOYED_SHA" "$BRANCH" | tee -a deploy-history.log
docker compose ps


