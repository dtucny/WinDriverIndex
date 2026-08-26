"""Polite HTTP client with snapshot-first fetching.

Every listing response is written to data/raw/{vendor}/{run_date}/ before it is
parsed, so parsers can be re-run against history without re-crawling.

Gigabyte (and possibly others) sit behind Akamai with TLS-fingerprint
filtering: plain curl/httpx get 403 site-wide, a Chrome-impersonating
handshake gets through (verified 2026-08-26). curl_cffi provides that. The
honest User-Agent header is still sent unless a vendor demands full browser
headers; flip `browser_headers` per vendor if a 403 shows up.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from curl_cffi import requests

from . import config


class PoliteClient:
    def __init__(self, vendor: str, run_date: str, *, browser_headers: bool = False,
                 impersonate: str | None = "chrome",
                 extra_headers: dict[str, str] | None = None):
        self.vendor = vendor
        self.run_date = run_date
        self.snapshot_dir = config.RAW_DIR / vendor / run_date
        self._session = requests.Session(impersonate=impersonate)
        if not browser_headers:
            self._session.headers["User-Agent"] = config.USER_AGENT
        if extra_headers:
            self._session.headers.update(extra_headers)
        self._last_request: dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        host = url.split("/")[2]
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < config.MIN_REQUEST_INTERVAL:
            time.sleep(config.MIN_REQUEST_INTERVAL - elapsed)
        self._last_request[host] = time.monotonic()

    def get(self, url: str, *, snapshot: str | None = None,
            timeout: float | None = None, **kwargs) -> requests.Response:
        """GET with per-host throttling; optionally snapshot the body to disk.

        `snapshot` is a filename relative to this run's snapshot directory.
        An existing snapshot from the same run is reused without a request,
        which makes interrupted crawls resumable for free.
        """
        path = self.snapshot_dir / snapshot if snapshot else None
        if path and path.exists():
            resp = _CachedResponse(path)
            return resp
        self._throttle(url)
        resp = self._session.get(url, timeout=timeout or config.REQUEST_TIMEOUT,
                                 **kwargs)
        resp.raise_for_status()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)
        return resp


class _CachedResponse:
    """Duck-types the two attributes parsers use, backed by a snapshot file."""

    status_code = 200

    def __init__(self, path: Path):
        self.content = path.read_bytes()

    def json(self):
        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")
