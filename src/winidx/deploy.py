"""Publish public/ to Cloudflare R2 (spec §8).

Layout: each run is copied to an immutable dated snapshot and `latest` is
synced to point at the same content:

    r2://{bucket}/v1/{date}/…   Cache-Control: immutable (cache forever)
    r2://{bucket}/v1/latest/…   Cache-Control: 1h, revalidate

Dated paths are content-stable so the CDN caches them for free and every
publish is reproducible; only `latest` ever needs revalidating. Consumers
pin `/v1/{date}/` for stability or follow `/v1/latest/` for freshness.

Credentials come only from the environment — nothing secret touches the repo
or an rclone config file (rclone is driven entirely via RCLONE_CONFIG_* vars):

    R2_ACCOUNT_ID          Cloudflare account id (for the S3 endpoint)
    R2_ACCESS_KEY_ID       R2 API token access key
    R2_SECRET_ACCESS_KEY   R2 API token secret
    R2_BUCKET              target bucket name
    R2_PUBLIC_BASE         optional: public base URL for the printed links
                           (custom domain or the bucket's r2.dev URL)

CORS and public access are one-time bucket settings — see `winidx deploy
--print-cors` and doc/findings.md.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys

from . import config

RCLONE = shutil.which("rclone") or "rclone"
IMMUTABLE = "Cache-Control: public, max-age=31536000, immutable"
# latest/ is the freshness path — 5 min keeps edge caching for read bursts
# while letting deploys propagate quickly (1h made every update look stale).
REVALIDATE = "Cache-Control: public, max-age=300, must-revalidate"

CORS_POLICY = """\
[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }
]
"""

_REQUIRED = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


def _rclone_env() -> dict:
    """rclone remote 'r2' defined purely through env vars (no config file)."""
    env = dict(os.environ)
    env.update({
        "RCLONE_CONFIG_R2_TYPE": "s3",
        "RCLONE_CONFIG_R2_PROVIDER": "Cloudflare",
        "RCLONE_CONFIG_R2_ACCESS_KEY_ID": os.environ["R2_ACCESS_KEY_ID"],
        "RCLONE_CONFIG_R2_SECRET_ACCESS_KEY": os.environ["R2_SECRET_ACCESS_KEY"],
        "RCLONE_CONFIG_R2_ENDPOINT":
            f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        # R2 has no ACLs; sending one makes uploads fail.
        "RCLONE_CONFIG_R2_NO_CHECK_BUCKET": "true",
        "RCLONE_S3_ACL": "",
    })
    return env


def _run(args: list[str], env: dict, dry_run: bool, log) -> None:
    cmd = [RCLONE, *args]
    if dry_run:
        cmd.append("--dry-run")
    log("+ " + " ".join(a if "SECRET" not in a else "***" for a in cmd))
    subprocess.run(cmd, env=env, check=True)


def run(*, dry_run: bool = False, date: str | None = None, log=print) -> dict:
    src = config.PUBLIC_DIR / "v1"
    if not src.is_dir():
        raise SystemExit(f"{src} not found — run `winidx publish` first")
    missing = [k for k in _REQUIRED if not os.environ.get(k)]
    if missing:
        raise SystemExit("missing env vars: " + ", ".join(missing)
                         + "\n(see `winidx deploy --print-cors` / module docstring)")

    bucket = os.environ["R2_BUCKET"]
    date = date or dt.date.today().isoformat()
    env = _rclone_env()
    common = ["--transfers", "32", "--checkers", "64", "--fast-list",
              "--s3-no-check-bucket"]

    # Immutable dated snapshot: copy (never delete) so history is preserved.
    # The ~36k per-HWID/per-board point-lookup files are excluded — they are
    # inherently "current" queries served from latest/, and snapshotting them
    # would add ~740 MB storage and ~36k Class A ops per dated path (the
    # aggregate files below are ~5 MB). Consumers pin dated aggregates.
    _run(["copy", str(src), f"r2:{bucket}/v1/{date}",
          "--exclude", "by-hwid/**", "--exclude", "by-board/**",
          "--header-upload", IMMUTABLE, *common], env, dry_run, log)
    # latest: sync (mirror, deleting removed families) with short TTL.
    _run(["sync", str(src), f"r2:{bucket}/v1/latest",
          "--header-upload", REVALIDATE, *common], env, dry_run, log)
    # Root-level pages (index.html, the human-readable landing page) → bucket
    # root. max-depth 1 keeps this to top-level files, not the v1/ tree.
    _run(["copy", str(config.PUBLIC_DIR), f"r2:{bucket}",
          "--max-depth", "1", "--header-upload", REVALIDATE, *common],
         env, dry_run, log)

    base = os.environ.get("R2_PUBLIC_BASE", "").rstrip("/")
    if base:
        log(f"\npublished:\n  {base}/v1/latest/water-level.json"
            f"\n  {base}/v1/{date}/water-level.json  (immutable snapshot)")
    else:
        log("\npublished to bucket "
            f"'{bucket}' under v1/latest and v1/{date} "
            "(set R2_PUBLIC_BASE to print URLs)")
    return {"bucket": bucket, "date": date, "dry_run": dry_run}
