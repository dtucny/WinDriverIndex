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


def test_display_inf_device_names():
    text = ('[Version]\r\nClass=Display\r\n[Models]\r\n'
            '%NVIDIA_DEV.2782% = Section001, PCI\\VEN_10DE&DEV_2782\r\n'
            '"%AMD744C.1%" = ati2mtag_N, PCI\\VEN_1002&DEV_744C&REV_C8 ; XTX\r\n'
            '[Strings]\r\nNVIDIA_DEV.2782 = "NVIDIA GeForce RTX 4070 Ti"\r\n'
            'AMD744C.1 = "AMD Radeon RX 7900 XTX"\r\n'
            '[Strings.0407]\r\nNVIDIA_DEV.2782 = "localised"\r\n')
    assert _parse_inf(text.encode())["names"] == {
        "PCI\\VEN_10DE&DEV_2782": "NVIDIA GeForce RTX 4070 Ti",
        "PCI\\VEN_1002&DEV_744C&REV_C8": "AMD Radeon RX 7900 XTX"}
    assert _parse_inf(text.replace("Display", "Net").encode())["names"] == {}


def test_burn_container_files_get_manifest_names(tmp_path):
    from winidx.extract import _burn_names
    split = tmp_path / "2.exe.x"
    (split / "2.cab.x").mkdir(parents=True)
    (split / "4.cab.x").mkdir()
    (split / "2.cab.x" / "0").write_bytes(
        b'<?xml version="1.0"?><BurnManifest><Payload Id="p1" '
        b'FilePath="WiFiDriver\\Netwtw08.INF" FileSize="3" Packaging="embedded" '
        b'SourcePath="a20" Container="WixAttachedContainer" /></BurnManifest>')
    (split / "4.cab.x" / "a20").write_bytes(b"inf")
    _burn_names(tmp_path)
    assert (split / "4.cab.x" / "WiFiDriver" / "Netwtw08.INF").read_bytes() == b"inf"
    assert not (split / "4.cab.x" / "a20").exists()


def test_zip_behind_a_pe_stub(tmp_path, monkeypatch):
    import io
    import zipfile
    from winidx import config
    from winidx.extract import _extract_and_index
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "tmp").mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("drivers/production/Windows10-x64/CyUcmClient.inf", SAMPLE)
    exe = tmp_path / "dell.exe"
    exe.write_bytes(b"MZ" + b"\0" * 4094 + buf.getvalue())   # Dell's self-extractor
    recs = _extract_and_index("ab" * 32, exe)
    assert [r["path"] for r in recs if "meta" in r] == [
        "drivers/production/Windows10-x64/CyUcmClient.inf"]
