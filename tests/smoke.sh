#!/usr/bin/env bash
set -euo pipefail

api_address=$(docker compose port api 8000)
web_address=$(docker compose port web 3000)

curl --fail --silent --show-error "http://${api_address}/healthz" | grep -Fx '{"status":"ok"}'
curl --fail --silent --show-error "http://${api_address}/readyz" | grep -Fx '{"status":"ready"}'
curl --fail --silent --show-error "http://${web_address}/" >/dev/null

docker compose exec -T postgres   psql -XAt -v ON_ERROR_STOP=1 -U memory -d memory   -c "SELECT '[1,2,3]'::vector" | grep -Fx '[1,2,3]'
docker compose exec -T redis redis-cli ping | grep -Fx 'PONG'
docker compose exec -T worker python -m worker.main --check
