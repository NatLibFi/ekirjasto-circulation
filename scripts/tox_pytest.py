from __future__ import annotations

import os
import subprocess
import sys


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def set_common_docker_env() -> None:
    db_host = require_env("DB_CIRC_HOST")
    db_port = require_env("DB_CIRC_5432_TCP_PORT")
    os.environ["SIMPLIFIED_TEST_DATABASE"] = (
        "postgresql://simplified_test:test@"
        f"{db_host}:{db_port}/simplified_circulation_test"
    )


def set_search_env() -> None:
    search_host = require_env("OS_CIRC_HOST")
    search_port = require_env("OS_CIRC_9200_TCP_PORT")
    os.environ["PALACE_TEST_SEARCH_URL"] = f"http://{search_host}:{search_port}"


def main() -> int:
    if len(sys.argv) < 2:
        raise RuntimeError("Usage: tox_pytest.py <api|core|migration> [pytest args...]")

    suite = sys.argv[1]
    pytest_args = sys.argv[2:]

    set_common_docker_env()
    if suite in {"api", "core"}:
        set_search_env()
    if suite == "api":
        os.environ[
            "ADMIN_EKIRJASTO_AUTHENTICATION_URL"
        ] = f"http://{require_env('OS_CIRC_HOST')}"
    if suite == "core":
        minio_host = require_env("MINIO_CIRC_HOST")
        minio_port = require_env("MINIO_CIRC_9000_TCP_PORT")
        os.environ[
            "PALACE_TEST_MINIO_ENDPOINT_URL"
        ] = f"http://{minio_host}:{minio_port}"

    return subprocess.call(["poetry", "run", "pytest", *pytest_args])


if __name__ == "__main__":
    raise SystemExit(main())
