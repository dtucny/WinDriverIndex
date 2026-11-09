import os
import time

from winidx import config, db, fetch


def test_already_stored_finds_non_zip_payloads(tmp_path, monkeypatch):
    # Dell payloads are stored as .EXE; the skip check once looked for .zip
    # only and re-downloaded every Dell package on every refresh.
    monkeypatch.setattr(config, "PAYLOAD_DIR", tmp_path)
    sha = "ab" + "0" * 62
    path = fetch.payload_path(sha, ".EXE")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    conn = db.connect(tmp_path / "index.sqlite")
    assert fetch._already_stored(conn, {"sha256": sha, "md5": None})
    assert not fetch._already_stored(conn, {"sha256": "cd" + "0" * 62, "md5": None})


def test_prune_drops_old_extracted_payloads_and_fetch_still_skips_them(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PAYLOAD_DIR", tmp_path / "payloads")
    conn = db.connect(tmp_path / "index.sqlite")
    old = time.time() - 40 * 86400
    paths = {}
    for name, extracted, mtime in (("old", True, old), ("new", True, None),
                                   ("raw", False, old)):
        sha = name.encode().hex().ljust(64, "0")
        paths[name] = p = fetch.payload_path(sha, ".zip")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        if mtime:
            os.utime(p, (mtime, mtime))
        if extracted:
            conn.execute("INSERT INTO payload_file VALUES (?, 'a.inf', ?, 1)", (sha, sha))
    assert fetch.prune(conn, 30, log=lambda *_: None) == {"pruned": 1, "gb": 0.0}
    assert not paths["old"].exists()
    assert paths["new"].exists() and paths["raw"].exists()   # grace / not yet extracted
    sha_old = paths["old"].name.split(".")[0]
    assert fetch._already_stored(conn, {"sha256": sha_old, "md5": None})
