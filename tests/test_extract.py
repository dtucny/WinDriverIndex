from winidx.extract import _decode_inf, _parse_inf

SAMPLE = """\
[Version]
Signature="$WINDOWS NT$"
Class=Net
ClassGUID={4d36e972-e325-11ce-bfc1-08002be10318}
Provider=%Mtk%
DriverVer=01/16/2026,3.5.0.1380
CatalogFile=netmtk.cat

[Mtk.NTamd64.10.0]
%RZ616.DeviceDesc% = RZ616, PCI\\VEN_14C3&DEV_0616&SUBSYS_16EC1462
%RZ608.DeviceDesc% = RZ608, PCI\\VEN_14C3&DEV_0608
"""


def test_parse_inf_fields():
    meta = _parse_inf(SAMPLE.encode())
    assert meta["driver_date"] == "2026-01-16"
    assert meta["driver_ver"] == "3.5.0.1380"
    assert meta["class"] == "Net"
    assert "PCI\\VEN_14C3&DEV_0616&SUBSYS_16EC1462" in meta["hwids"]
    assert "PCI\\VEN_14C3&DEV_0608" in meta["hwids"]


def test_decode_utf16():
    data = "﻿[Version]\r\nDriverVer=06/29/2024,3.4.0.1063\r\n".encode("utf-16-le")
    meta = _parse_inf(data)
    assert meta["driver_date"] == "2024-06-29"
    assert meta["driver_ver"] == "3.4.0.1063"


def test_decode_ansi_fallback():
    assert "caf" in _decode_inf(b"; caf\xe9\r\n")


def _fixture_db(tmp_path, monkeypatch):
    from winidx import config, db
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PAYLOAD_DIR", tmp_path / "payloads")
    conn = db.connect(tmp_path / "index.sqlite")
    return conn


def _add_payload(conn, sha, data: bytes, ext=".zip"):
    from winidx.fetch import payload_path
    p = payload_path(sha, ext)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    conn.execute(
        "INSERT INTO artefact (vendor, vendor_artefact_id, kind, sha256, url,"
        " first_seen, last_seen) VALUES ('asus', ?, 'driver', ?, 'https://x/' || ?,"
        " '2026-09-23', '2026-09-23')", (sha, sha, sha))


def test_corrupt_payload_is_quarantined_not_retried(tmp_path, monkeypatch):
    import zipfile
    from winidx import extract
    conn = _fixture_db(tmp_path, monkeypatch)
    bad = "b" * 64
    good = "a" * 64
    _add_payload(conn, bad, b"PK\x03\x04 definitely not a zip")
    zpath = tmp_path / "good.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("x/y.inf", SAMPLE)
    _add_payload(conn, good, zpath.read_bytes())

    logs = []
    stats = extract.run(conn, log=logs.append)
    assert stats["extracted"] == 1 and stats["quarantined"] == 1
    assert stats["failed"] == 0            # one corrupt file is not systemic
    assert conn.execute("SELECT COUNT(*) FROM payload_quarantine").fetchone()[0] == 1
    assert any("QUARANTINED" in line for line in logs)

    # Second run: the corrupt payload is skipped, not re-attempted.
    stats = extract.run(conn, log=logs.append)
    assert stats["quarantined"] == 0 and stats["skipped_quarantined"] == 1

    # --retry-quarantined clears the parking and re-attempts (and re-fails).
    stats = extract.run(conn, retry_quarantined=True, log=logs.append)
    assert stats["quarantined"] == 1 and stats["skipped_quarantined"] == 0


def test_all_failures_is_systemic(tmp_path, monkeypatch):
    from winidx import extract
    conn = _fixture_db(tmp_path, monkeypatch)
    _add_payload(conn, "c" * 64, b"garbage")
    stats = extract.run(conn, log=lambda *_: None)
    assert stats["failed"] == 1            # nothing succeeded -> stage fails
