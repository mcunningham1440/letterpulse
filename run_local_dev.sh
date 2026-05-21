#!/bin/bash
# Local dev runner: starts a local Postgres container, overrides the cloud
# DB env vars in this shell only (cloud .env is left untouched), runs
# migrations, and starts the Django dev server from the project venv.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTAINER_NAME="letterpulse_pg"
VOLUME_NAME="letterpulse_pg_data"
PG_IMAGE="postgres:16"
PG_DB="letterpulse"
PG_USER="letterpulse"
PG_PASSWORD="devpassword"
# Host-side port. We use 5433 (not 5432) because there's commonly a native
# Postgres bound to 127.0.0.1:5432 on dev machines, and loopback bindings
# win over wildcard bindings on macOS, which would silently route Django's
# connections to the wrong DB.
PG_PORT="5433"

# 1. Ensure Postgres container is running (idempotent).
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Postgres container '${CONTAINER_NAME}' already running."
elif docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Starting existing Postgres container '${CONTAINER_NAME}'..."
    docker start "$CONTAINER_NAME" >/dev/null
else
    echo "Creating Postgres container '${CONTAINER_NAME}'..."
    docker run -d \
        --name "$CONTAINER_NAME" \
        -p "${PG_PORT}:5432" \
        -e "POSTGRES_DB=${PG_DB}" \
        -e "POSTGRES_USER=${PG_USER}" \
        -e "POSTGRES_PASSWORD=${PG_PASSWORD}" \
        -v "${VOLUME_NAME}:/var/lib/postgresql/data" \
        "$PG_IMAGE" >/dev/null
fi

# 2. Wait for Postgres to accept connections (max ~30s).
echo "Waiting for Postgres to be ready..."
for i in {1..30}; do
    if docker exec "$CONTAINER_NAME" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
        echo "Postgres is ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "ERROR: Postgres did not become ready in 30s." >&2
        exit 1
    fi
    sleep 1
done

# 3. Override cloud DB env vars for this shell only. settings.py uses
#    python-dotenv's load_dotenv(), which by default does NOT override
#    existing environment variables — so these exports take precedence
#    over .env without modifying the file.
export DB_HOST=localhost
export DB_PORT="${PG_PORT}"
export DATABASE_SECRET="{\"username\":\"${PG_USER}\",\"password\":\"${PG_PASSWORD}\"}"

# 4. Activate venv and run migrate + dev server.
# shellcheck disable=SC1091
source "$SCRIPT_DIR/.venv/bin/activate"

python "$SCRIPT_DIR/manage.py" migrate
exec python "$SCRIPT_DIR/manage.py" runserver
