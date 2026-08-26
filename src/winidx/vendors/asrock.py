"""ASRock crawler (spec §4.3).

Server-rendered ASP behind Imperva Incapsula. Two ways in:

1. **Camoufox (primary).** A headless anti-detect Firefox passes the JS
   challenge unaided on BOTH hosts — verified 2026-08-26. First-time setup:
   `uv run python -m camoufox fetch`. Cookie relay cannot substitute for
   www.asrock.com: its Incapsula session is bound to the solving browser's
   fingerprint, so replayed cookies re-challenge (pg is more lenient, but
   relying on that is fragile). Must use a no_viewport context to dodge a
   Browser.setDefaultViewport protocol error in the current camoufox build.
2. **Cookie relay (fallback).** If camoufox isn't installed, cookies in
   data/asrock_cookies.txt (line 1 = Cookie header, line 2 = User-Agent)
   replay via curl. Works for pg; www usually re-challenges. A host whose
   session is stale is skipped, not fatal.

Payload downloads (download.asrock.com, CloudFront) are unchallenged and stay
on the normal fetch path.

Enumeration: /mb/index.asp embeds `allmodels=[[model, socket, 'Vendor
Chipset', form], ...]` inline; the site's own JS builds board URLs as
{chipset vendor}/{model minus first '/'}/, which this crawler mirrors.

Identity: the Global download URL — ASRock URLs are version-addressed per
artefact (shared across boards; but see doc/findings.md: a few payloads were
repacked in place, so the published SHA-256 is advisory).
"""

from __future__ import annotations

import html as html_mod
import re
import sqlite3
import time

from selectolax.parser import HTMLParser

from .. import config, db, scope, versions
from ..http import PoliteClient

VENDOR = "asrock"
HOSTS = ["https://pg.asrock.com", "https://www.asrock.com"]
COOKIE_FILE = config.DATA_DIR / "asrock_cookies.txt"
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) "
              "Gecko/20100101 Firefox/154.0")

_ALLMODELS = re.compile(r"allmodels\s*=\s*(\[\[.*?\]\])\s*;", re.DOTALL)
_MODEL_ROW = re.compile(r"\['((?:[^'\\]|\\.)*)','((?:[^'\\]|\\.)*)',"
                        r"'((?:[^'\\]|\\.)*)','((?:[^'\\]|\\.)*)'\]")
_SHA256 = re.compile(r"SHA256:\s*([0-9a-fA-F]{64})")
_VER = re.compile(r"ver:\s*([^\s<]+)")
_CLICKRATE = re.compile(r"ClickRate\('((?:[^'\\]|\\.)*)',\s*'((?:[^'\\]|\\.)*)'")

BIOS_CATEGORY = re.compile(r"\bbios\b", re.IGNORECASE)
UTILITY_CATEGORY = re.compile(r"utilit|app shop|software", re.IGNORECASE)


class _BrowserResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.status_code = 200

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class BrowserClient:
    """PoliteClient-compatible facade over a headless Camoufox page.

    Same snapshot-first, per-host-throttled contract; the Incapsula
    challenge resolves itself during the first page load on each host.
    """

    def __init__(self, vendor: str, run_date: str):
        self.snapshot_dir = config.RAW_DIR / vendor / run_date
        self._cm = self._page = None
        self._last_request = 0.0

    def _ensure_page(self):
        if self._page is None:
            from camoufox.sync_api import Camoufox
            self._cm = Camoufox(headless=True)
            browser = self._cm.__enter__()
            # no_viewport avoids a Browser.setDefaultViewport protocol error
            # with camoufox 0.4.11 + its current browser build (see findings).
            self._page = browser.new_context(no_viewport=True).new_page()
        return self._page

    def get(self, url: str, *, snapshot: str | None = None) -> _BrowserResponse:
        path = self.snapshot_dir / snapshot if snapshot else None
        if path and path.exists():
            return _BrowserResponse(path.read_bytes())
        elapsed = time.monotonic() - self._last_request
        if elapsed < config.MIN_REQUEST_INTERVAL:
            time.sleep(config.MIN_REQUEST_INTERVAL - elapsed)
        page = self._ensure_page()
        page.goto(url, timeout=90_000)
        html = page.content()
        for _ in range(20):                       # challenge needs a few seconds
            if "_Incapsula_" not in html and "Incapsula incident" not in html:
                break
            page.wait_for_timeout(3000)
            html = page.content()               # iframe self-refreshes in place
        self._last_request = time.monotonic()
        if "_Incapsula_" in html or "Incapsula incident" in html:
            raise _Challenged(url)
        content = html.encode("utf-8")
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return _BrowserResponse(content)

    def close(self) -> None:
        if self._cm is not None:
            self._cm.__exit__(None, None, None)
            self._cm = self._page = None


class _CookieClient(PoliteClient):
    """PoliteClient with browser cookies; raises _Challenged on a challenge
    page so the crawler can skip that host instead of storing a bad snapshot."""

    def get(self, url, *, snapshot=None, **kwargs):
        if snapshot and (self.snapshot_dir / snapshot).exists():
            return super().get(url, snapshot=snapshot, **kwargs)  # cache hit
        resp = super().get(url, snapshot=None, **kwargs)          # fetch, don't store
        if b"_Incapsula_" in resp.content or b"Incapsula incident" in resp.content:
            raise _Challenged(url)
        if snapshot:
            path = self.snapshot_dir / snapshot
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)
        return resp


def make_client(run_date: str):
    try:
        import camoufox.sync_api  # noqa: F401
        return BrowserClient(VENDOR, run_date)
    except ImportError:
        pass
    if COOKIE_FILE.exists():
        lines = COOKIE_FILE.read_text().strip().splitlines()
        return _CookieClient(
            VENDOR, run_date, browser_headers=True, impersonate=None,
            extra_headers={
                "Cookie": lines[0].strip(),
                "User-Agent": lines[1].strip() if len(lines) > 1 else DEFAULT_UA,
                "Accept-Language": "en-GB,en;q=0.8"})
    raise SystemExit(
        "asrock: install camoufox (`uv run python -m camoufox fetch`) or "
        f"provide {COOKIE_FILE} — see doc/findings.md")


def crawl(conn: sqlite3.Connection, client, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    try:
        return _crawl(conn, client, run_date, limit=limit, log=log)
    finally:
        if hasattr(client, "close"):
            client.close()


def _crawl(conn: sqlite3.Connection, client, run_date: str,
           *, limit: int | None, log) -> dict:
    boards: dict[str, tuple] = {}
    for host in HOSTS:
        try:
            models = _enumerate(client, host)
        except _Challenged:
            log(f"asrock: {host} index did not clear Incapsula this run — skipping")
            continue
        for model, socket_raw, chipset_str, _form in models:
            hit = scope.extract_chipset(chipset_str)
            if hit and model not in boards:
                boards[model] = (host, chipset_str, *hit)
    log(f"asrock: {len(boards)} in-scope boards enumerated")
    items = sorted(boards.items())
    if limit:
        items = items[:limit]

    n_boards = n_artefacts = n_new = 0
    for model, (host, chipset_str, chipset, socket) in items:
        vendor_path = chipset_str.split(" ")[0]           # AMD | Intel
        slug = model.replace("/", "", 1)                  # mirrors the site JS
        url = f"{host}/mb/{vendor_path}/{slug}/Download.html"
        try:
            page = client.get(url.replace(" ", "%20"),
                              snapshot=f"dl_{slug.replace(' ', '_').replace('+', 'p')}.html")
        except _Challenged:
            log(f"asrock: skipped {model} — challenge did not clear")
            continue
        board_id = db.upsert_board(
            conn, run_date, vendor=VENDOR, vendor_product_id=model,
            name=model, slug=slug, chipset=chipset, socket=socket,
            support_url=url)
        n_boards += 1
        for entry in _parse_download_page(page.content.decode("utf-8", "replace")):
            recorded = _record_artefact(conn, run_date, board_id, entry)
            if recorded is not None:
                n_artefacts += 1
                n_new += recorded
        conn.commit()
    log(f"asrock: {n_boards} boards, {n_artefacts} listings, {n_new} new artefacts")
    return {"boards": n_boards, "listings": n_artefacts, "new_artefacts": n_new}


class _Challenged(Exception):
    pass


def _enumerate(client, host: str) -> list[tuple]:
    resp = client.get(f"{host}/mb/index.asp",
                      snapshot=f"index_{host.split('//')[1].split('.')[0]}.html")
    text = resp.content.decode("utf-8", "replace")
    m = _ALLMODELS.search(text)
    if not m:
        raise RuntimeError(f"allmodels array not found on {host}/mb/index.asp")
    return [t.groups() for t in _MODEL_ROW.finditer(m.group(1))]


def _parse_download_page(page: str):
    tree = HTMLParser(page)
    for tr in tree.css("tr"):
        classes = tr.attributes.get("class") or ""
        link = next((a for a in tr.css("a")
                     if (a.attributes.get("href") or "").startswith("https://download.asrock.com/")),
                    None)
        if link is None:
            continue
        # Win11-only scope; the Beta Zone rows carry class 'Beta' instead.
        is_beta = "Beta" in classes.split()
        if "osW1164" not in classes and not is_beta:
            continue
        cells = tr.css("td")
        desc_html = cells[0].html if cells else ""
        desc_text = cells[0].text(separator=" ", strip=True) if cells else ""
        onclick = link.attributes.get("onClick") or link.attributes.get("onclick") or ""
        click = _CLICKRATE.search(onclick)
        sha = _SHA256.search(desc_html or "")
        ver = _VER.search(html_mod.unescape(desc_text))
        size = date = None
        if len(cells) >= 4:
            size = _parse_size(cells[2].text(strip=True))
            date = _parse_date(cells[3].text(strip=True))
        yield {
            "url": link.attributes["href"],
            "category": html_mod.unescape(click.group(1)) if click else None,
            "version": (click.group(2) if click else None) or (ver.group(1) if ver else None),
            "sha256": sha.group(1).lower() if sha else None,
            "description": desc_text[:500],
            "size": size,
            "date": date,
            "is_beta": is_beta or "[beta]" in desc_text.lower(),
        }


def _parse_size(text: str) -> int | None:
    m = re.match(r"([\d.]+)\s*(KB|MB|GB)", text or "", re.IGNORECASE)
    return int(float(m.group(1)) * {"kb": 1e3, "mb": 1e6, "gb": 1e9}[m.group(2).lower()]) \
        if m else None


def _parse_date(text: str) -> str | None:
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", text or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def _record_artefact(conn, run_date, board_id, entry) -> bool | None:
    category = entry["category"] or ""
    # The download URL path is the authoritative taxonomy
    # (/Drivers/, /BIOS/, /Utility/, /App/); category names are free text.
    path = entry["url"].lower()
    if "/bios/" in path or BIOS_CATEGORY.search(category):
        kind = "bios"
    elif "/utility/" in path or "/app/" in path or UTILITY_CATEGORY.search(category):
        kind = "utility"
    else:
        kind = "driver"
    ver = versions.parse(entry["version"])
    artefact_id, is_new = db.upsert_artefact(
        conn, run_date, vendor=VENDOR,
        vendor_artefact_id=entry["url"],
        kind=kind,
        component_hint=category or None,
        version_raw=entry["version"],
        version_normalised=ver.normalised_json,
        release_date=entry["date"],
        file_size=entry["size"],
        url=entry["url"],
        sha256=entry["sha256"],
        os_raw="Win11 64",
        is_beta=int(entry["is_beta"] or ver.is_beta),
        description_text=entry["description"],
    )
    db.link_board_artefact(conn, run_date, board_id, artefact_id, entry["date"])
    return is_new
