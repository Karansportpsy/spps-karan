#!/bin/bash
# Start a THROWAWAY local PostgreSQL with TLS for the E2E suite (no Docker needed; needs the postgres server binaries).
# Usage: tests_e2e/pg_tls_cluster.sh [port]   -> prints the SPPS_E2E_ADMIN_URL to export.
set -euo pipefail
PORT=${1:-55440}
DIR=${SPPS_E2E_PGDIR:-/tmp/spps_e2e_pg}
BIN=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)
[ -n "$BIN" ] || BIN=$(pg_config --bindir)
if [ ! -d "$DIR" ]; then
  "$BIN/initdb" -D "$DIR" -U postgres --auth=trust >/dev/null
  openssl req -new -x509 -days 30 -nodes -subj "/CN=localhost" -out "$DIR/server.crt" -keyout "$DIR/server.key" 2>/dev/null
  chmod 600 "$DIR/server.key"
  printf "ssl = on\nlisten_addresses = '127.0.0.1'\nport = %s\nunix_socket_directories = '%s'\n" "$PORT" "$DIR" >> "$DIR/postgresql.conf"
fi
"$BIN/pg_ctl" -D "$DIR" -l "$DIR/log.txt" -w start >/dev/null || true
echo "export SPPS_E2E_ADMIN_URL='postgresql://postgres@127.0.0.1:$PORT/postgres?sslmode=require'"
echo "export SPPS_E2E_DESTROY_SCHEMAS=yes"
echo "# stop with: $BIN/pg_ctl -D $DIR stop"
