import json

from winidx import hwids
from winidx.extract import _parse_inf


def test_string_keys_and_registry_paths_are_not_hwids():
    for junk in (r"PCI\VEN_8086&DEV_09AB.DEVICEDESC", r"USB\CLASS_01.DEVICEDESC",
                 r"PCI\AMDPCIE.DEVICEDESC", r"PCI\PARAMETERS", r"ACPI\PARAMETERS",
                 r"PCI\VID_8087&PID_0B67"):
        assert not hwids.is_hwid(junk), junk
    for real in (r"PCI\VEN_10EC&DEV_8125&SUBSYS_7C961462&REV_05",
                 r"SWC\AMDOCL-23.19", r"ACPI\INT33A1", r"USB\VID_0489&PID_E0FA",
                 r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0897"):
        assert hwids.is_hwid(real), real


def test_generic_ids_have_no_device_part():
    for g in (r"PCI\CC_010802", r"PCI\VEN_8086&CC_0108", r"USB\CLASS_01",
              r"HDAUDIO\FUNC_01&VEN_10EC"):
        assert hwids.is_generic(g), g
    for s in (r"PCI\VEN_8086&DEV_4D28&CC_0401",
              r"USB\ASMEDIAROOT_HUB&VID1B21&PID1040&VER01165401",
              r"INTELAUDIO\CTLR_DEV_51C8&LINKTYPE_02&DEVTYPE_00&VEN_8086"):
        assert not hwids.is_generic(s), s


def test_base_drops_subsys_and_rev():
    assert hwids.base(r"PCI\VEN_10EC&DEV_8125&SUBSYS_7C961462&REV_05") \
        == r"PCI\VEN_10EC&DEV_8125"
    assert hwids.base(r"USB\VID_0489&PID_E0FA&REV_0100&MI_00") \
        == r"USB\VID_0489&PID_E0FA&MI_00"


def test_parse_inf_skips_string_keys():
    inf = ("[Version]\nDriverVer=01/16/2026,1.0\n[M.NTamd64]\n"
           "%PCI\\VEN_8086&DEV_09AB.DeviceDesc% = S, PCI\\VEN_8086&DEV_09AB\n"
           "[Strings]\nPCI\\VEN_8086&DEV_09AB.DeviceDesc = \"x\"\n")
    assert _parse_inf(inf.encode())["hwids"] == [r"PCI\VEN_8086&DEV_09AB"]


def _db(tmp_path, monkeypatch):
    from winidx import config, db
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return db.connect(tmp_path / "index.sqlite")


def _family(conn, name, comp="lan"):
    return conn.execute("INSERT INTO family (name, silicon_vendor, component)"
                        " VALUES (?, 'x', ?)", (name, comp)).lastrowid


def _payload(conn, fid, sha, infs):
    conn.execute("INSERT INTO artefact (vendor, vendor_artefact_id, family_id,"
                 " sha256, first_seen, last_seen) VALUES ('v', ?, ?, ?, 'd', 'd')",
                 (sha, fid, sha))
    for name, ids in infs.items():
        conn.execute("INSERT INTO inf (payload_sha256, path, inf_sha256, hwids)"
                     " VALUES (?, ?, ?, ?)", (sha, f"x/{name}", name + sha,
                                              json.dumps(ids)))


def test_hwid_support_ownership(tmp_path, monkeypatch):
    from winidx.families import hwid_support
    conn = _db(tmp_path, monkeypatch)
    lan25 = _family(conn, "Realtek 8125 LAN")
    lan68 = _family(conn, "Realtek 8168 LAN")
    wifi = _family(conn, "Realtek Wi-Fi", "wlan")
    shared = [r"PCI\VEN_10EC&DEV_8125", r"PCI\VEN_10EC&DEV_8168",
              r"PCI\VEN_10EC&DEV_8136", r"PCI\CC_020000"]
    for i in range(4):   # both LAN families ship the combined INF every time
        _payload(conn, lan25, f"a{i}", {"rt640x64.inf": shared})
        _payload(conn, lan68, f"b{i}", {"rt640x64.inf": shared})
    # Wi-Fi ships its own INF; the LAN INF rides along in 1 of 4 packages
    for i in range(4):
        infs = {"netrtwlane.inf": [r"PCI\VEN_10EC&DEV_C852"]}
        if i == 0:
            infs["rt640x64.inf"] = shared
        _payload(conn, wifi, f"c{i}", infs)

    sup = hwid_support(conn)
    assert set(sup[lan25]) == {r"PCI\VEN_10EC&DEV_8125", r"PCI\VEN_10EC&DEV_8136"}
    assert set(sup[lan68]) == {r"PCI\VEN_10EC&DEV_8168", r"PCI\VEN_10EC&DEV_8136"}
    assert set(sup[wifi]) == {r"PCI\VEN_10EC&DEV_C852"}   # no ride-along, no CC_
