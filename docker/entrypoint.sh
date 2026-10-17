#!/bin/sh
set -e
mkdir -p /app/data
chown -R repona:repona /app/data
if [ "$(id -u)" = 0 ]; then
  exec setpriv --reuid=repona --regid=repona --init-groups "$@"
fi
exec "$@"
