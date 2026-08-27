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
