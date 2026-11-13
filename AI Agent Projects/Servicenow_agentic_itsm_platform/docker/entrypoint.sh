#!/bin/sh
# Optionally apply database migrations, then run the requested command.
set -e

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "Applying database migrations..."
  alembic upgrade head
fi

exec "$@"
