#!/bin/sh
# Bring the venv in line with the mounted checkout (a no-op unless uv.lock
# moved since the image was built), then run the command.
set -e
uv sync --frozen --quiet
exec "$@"
