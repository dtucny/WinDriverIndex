import sqlite3

from winidx import config, fetch


def test_already_stored_finds_non_zip_payloads(tmp_path, monkeypatch):
    # Dell payloads are stored as .EXE; the skip check once looked for .zip
    # only and re-downloaded every Dell package on every refresh.
    monkeypatch.setattr(config, "PAYLOAD_DIR", tmp_path)
    sha = "ab" + "0" * 62
    path = fetch.payload_path(sha, ".EXE")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert fetch._already_stored(conn, {"sha256": sha, "md5": None})
    assert not fetch._already_stored(conn, {"sha256": "cd" + "0" * 62, "md5": None})
