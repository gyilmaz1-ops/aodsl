#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
docker compose -f deploy/docker-compose.certification.yml up -d --wait
trap 'docker compose -f deploy/docker-compose.certification.yml down -v' EXIT
export AODSL_POSTGRES_DSN="postgresql://aodsl:aodsl_cert@127.0.0.1:55432/aodsl_cert"
python -m pip install -e ".[postgres,test]"
python tools/run_live_postgres_certification.py
