#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="docker-compose-tox.yml"
SERVICE="tox"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/tox-docker.sh build
  ./scripts/tox-docker.sh api [-- test_path ...]
  ./scripts/tox-docker.sh core [-- test_path ...]
  ./scripts/tox-docker.sh all

Examples:
  ./scripts/tox-docker.sh build
  ./scripts/tox-docker.sh api
  ./scripts/tox-docker.sh core
  ./scripts/tox-docker.sh all
  ./scripts/tox-docker.sh api -- tests/api/opds/test_opds2.py
  ./scripts/tox-docker.sh core -- tests/core/test_circulation_data.py
EOF
}

cmd_build() {
  docker compose -f "$COMPOSE_FILE" build "$SERVICE"
}

cmd_api() {
  if [[ "$#" -gt 0 ]]; then
    docker compose -f "$COMPOSE_FILE" run --rm "$SERVICE" tox -e py311-api-docker -- "$@"
  else
    docker compose -f "$COMPOSE_FILE" run --rm "$SERVICE" tox -e py311-api-docker
  fi
}

cmd_core() {
  if [[ "$#" -gt 0 ]]; then
    docker compose -f "$COMPOSE_FILE" run --rm "$SERVICE" tox -e py311-core-docker -- "$@"
  else
    docker compose -f "$COMPOSE_FILE" run --rm "$SERVICE" tox -e py311-core-docker
  fi
}

cmd_all() {
  docker compose -f "$COMPOSE_FILE" run --rm "$SERVICE" tox -e py311-api-docker,py311-core-docker
}

case "${1:-}" in
  build) shift; cmd_build "$@" ;;
  api)   shift; cmd_api "$@" ;;
  core)  shift; cmd_core "$@" ;;
  all)   shift; cmd_all "$@" ;;
  -h|--help|"") usage ;;
  *) echo "Unknown command: $1"; usage; exit 1 ;;
esac
