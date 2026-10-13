#!/bin/sh
set -e
mkdir -p /app/data
chown -R repona:repona /app/data
exec runuser -u repona -- "$@"
