#!/bin/sh
# One refresh: the stages ops/winidx-weekly.service runs, for the container.
# A failing stage stops the run before publish, so the published index never
# goes stale-but-wrong silently.
set -e
uv run winidx crawl
uv run winidx fetch
uv run winidx extract
uv run winidx assign
uv run winidx publish
# Extracted payloads past the grace period; unset to keep them all.
if [ -n "$WINIDX_KEEP_DAYS" ]; then
    uv run winidx prune --keep-days "$WINIDX_KEEP_DAYS"
fi
if [ -n "$R2_BUCKET" ]; then
    uv run winidx deploy
else
    echo "refresh: no R2 credentials, skipping deploy"
fi
