import datetime as dt

from winidx.bios import agesa_key, parse_agesa


def test_parse_gigabyte():
    assert parse_agesa("Update AM4 AGESA ComboV2 1.2.0.12") == ("V2", "1.2.0.12", "")


def test_parse_msi():
    assert parse_agesa("- Updated AMD AGESA ComboAm4v2PI 1.0.0.2") == ("V2", "1.0.0.2", "")


def test_parse_asus_patch():
    line, ver, patch = parse_agesa(
        "Updated AGESA ComboAM5 PI 1.3.0.1b Patch A to support TSME")
    assert (line, ver, patch) == ("AM5", "1.3.0.1b", "A")


def test_parse_bare():
    assert parse_agesa("Update AGESA 1.2.0.3c") == ("", "1.2.0.3c", "")


def test_letters_rank_above_digits():
    assert agesa_key("1.2.0.A") > agesa_key("1.2.0.8")
    assert agesa_key("1.2.0.Ca") > agesa_key("1.2.0.C")
    assert agesa_key("1.2.0.B") > agesa_key("1.2.0.A")


def test_patch_ranks_above_base():
    assert agesa_key("1.3.0.1b", "A") > agesa_key("1.3.0.1b")


def test_none_when_absent():
    assert parse_agesa("Improve system stability") is None
    assert parse_agesa(None) is None


def test_clean_version():
    from winidx.bios import _clean_version
    assert _clean_version("7D76vAH2(Beta version)") == "7D76vAH2"
    assert _clean_version("02.21.00 A 1") == "02.21.00"
    assert _clean_version("M3CN50WW") == "M3CN50WW"
    assert _clean_version("-") is None


def test_last_bios_version(tmp_path):
    from winidx import bios, db
    conn = db.connect(tmp_path / "t.sqlite")
    bid = db.upsert_board(conn, "2026-09-01", vendor="asus",
                          vendor_product_id="b1", name="B1", slug="b1")

    def add(vid, ver, date, hint="BIOS", beta=0):
        aid, _ = db.upsert_artefact(conn, "2026-09-01", vendor="asus",
                                    vendor_artefact_id=vid, kind="bios",
                                    component_hint=hint, version_raw=ver,
                                    release_date=date, is_beta=beta)
        db.link_board_artefact(conn, "2026-09-01", bid, aid, date)
    add("a", "1205", "2026-05-01")
    add("b", "1302", "2026-07-01")
    add("c", "1401", "2026-08-01", beta=1)            # activity, not version
    add("d", "16.1.40.2765v3", "2026-09-01", hint="Intel ME")   # not a BIOS
    b = bios.compute(conn, today=dt.date(2026, 9, 29))["per_board"][bid]
    assert b["last_bios"] == "2026-08-01"
    assert b["last_bios_version"] == "1302"
    assert b["last_bios_version_date"] == "2026-07-01"
