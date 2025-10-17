#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="docker-compose-tox.yml"
SERVICE="tox"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/tox-docker.sh build
  ./scripts/tox-docker.sh api
  ./scripts/tox-docker.sh core
  ./scripts/tox-docker.sh all
  ./scripts/tox-docker.sh <test_path> [pytest args...]

Examples:
  ./scripts/tox-docker.sh build
  ./scripts/tox-docker.sh api
  ./scripts/tox-docker.sh core
  ./scripts/tox-docker.sh all
  ./scripts/tox-docker.sh tests/api/test_odl2.py
  ./scripts/tox-docker.sh tests/api/test_odl2.py test_import
  ./scripts/tox-docker.sh tests/core/test_circulation_data.py::TestCirculationData
EOF
}

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

case "${1:-}" in
  build)
    dc build "$SERVICE"
    ;;
  api)
    dc run --rm "$SERVICE" tox -e py311-api-docker
    ;;
  core)
    dc run --rm "$SERVICE" tox -e py311-core-docker
    ;;
  all)
    dc run --rm "$SERVICE" tox -e py311-api-docker,py311-core-docker
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    # Run single test (auto-detect api/core by path)
    [[ "${1:-}" == "--" ]] && shift
    if [[ "${1:-}" == tests/core/* ]]; then
      env="py311-core-docker"
    else
      env="py311-api-docker"
    fi
    dc run --rm "$SERVICE" tox -e "$env" -- "$@"
    ;;
esac
