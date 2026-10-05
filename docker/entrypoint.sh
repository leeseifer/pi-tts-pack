#!/bin/sh
set -eu
if [ "$#" -ge 3 ] && [ "$1" = python ] && [ "$2" = -m ] && [ "$3" = app.main ]; then
  python docker/prepare_models.py
fi
exec "$@"
