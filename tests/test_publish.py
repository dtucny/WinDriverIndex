from winidx.publish import _MSI_CODE, _equiv, _parallel, _water_lines
from winidx.versions import parse


def test_nvidia_equiv():
    assert _equiv("NVIDIA Graphics", "32.0.15.9186") == "591.86"
    assert _equiv("NVIDIA Graphics", "32.0.16.1088 ") == "610.88"
    assert _equiv("NVIDIA Graphics", "32.0.101.6881") is None   # Intel scheme
    assert _equiv("Intel VGA", "32.0.15.9186") is None


def test_realtek_uad_equiv():
    assert _equiv("Realtek Audio", "10007.1_UAD_WHQL") == "6.0.10007.1"
    assert _equiv("Realtek Audio", "9999.2 uad") == "6.0.9999.2"
    assert _equiv("Realtek Audio", "6.0.9954.3") is None


def test_parallel_lines():
    assert _parallel(["2023-01-01", "2025-06-01"], ["2024-01-01", "2026-01-01"])
    assert not _parallel(["2020-01-01", "2022-01-01"], ["2023-01-01", "2026-01-01"])
    assert _parallel(None, ["2023-01-01", "2026-01-01"])


def test_water_lines():
    fam_lines = {
        32: {"top": parse("32.0.31041.1004"), "date": "2026-08-01",
             "disp": "32.0.31041.1004", "span": ["2023-01-01", "2026-08-01"]},
        25: {"top": parse("25.8.1"), "date": "2026-07-01", "disp": "25.8.1",
             "span": ["2024-01-01", "2026-07-01"]},
        21: {"top": parse("21.1.0"), "date": "2021-01-01", "disp": "21.1.0",
             "span": ["2019-01-01", "2021-01-01"]},
    }
    got = _water_lines({"family": "AMD Graphics", "version_normalised": [32, 0, 31041, 1004]}, fam_lines)
    assert [x["major"] for x in got] == [32, 25, 21]
    assert got[0]["water_line"] and not got[0]["parallel_to_water"]
    assert got[1]["parallel_to_water"] and not got[2]["parallel_to_water"]


def test_msi_board_code():
    assert _MSI_CODE.match("7C96v1L9").group(1) == "7C96"
    assert _MSI_CODE.match("7E15") is None


def test_parallel_needs_real_overlap():
    # AMD's last 31.x build landed a week after the first 32.x: a handover
    assert not _parallel(["2022-09-15", "2024-06-03"], ["2024-05-28", "2026-09-03"])


def test_realtek_dual_spelling_folds_into_one_row():
    from winidx.publish import _fold_realtek_aliases

    def row(ver, fams, pub):
        return {"driver_ver": ver, "driver_date": "2026-04-07",
                "driver_ver_aliases": [], "family_ids": set(fams),
                "artefact_ids": set(fams), "hwids": {"PCI\\VEN_10EC&DEV_8125"},
                "first_published": pub}
    groups = {("rt640x64.inf", "10.080.0407.2026"): row("10.080.0407.2026", {1}, "2026-09-07"),
              ("rt640x64.inf", "10.080.50.0407"): row("10.080.50.0407", {2}, "2026-09-16"),
              ("rt640x64.inf", "10.080.0301.2026"): row("10.080.0301.2026", {3}, None)}
    groups[("rt640x64.inf", "10.080.0301.2026")]["driver_date"] = "2026-03-01"
    _fold_realtek_aliases(groups)
    assert set(groups) == {("rt640x64.inf", "10.080.50.0407"),
                           ("rt640x64.inf", "10.080.0301.2026")}   # no twin: kept
    merged = groups[("rt640x64.inf", "10.080.50.0407")]
    assert merged["driver_ver_aliases"] == ["10.080.0407.2026"]
    assert merged["family_ids"] == {1, 2} and merged["first_published"] == "2026-09-07"


def test_inf_series():
    from winidx.publish import _inf_series
    assert _inf_series("u0403049.inf") == "u*.inf"
    assert _inf_series("amdwin-u0199286.inf") == "amdwin-u*.inf"
    assert _inf_series("rt640x64.inf") == "rt640x64.inf"
    assert _inf_series(None) is None


def test_mediatek_chip_named_in_listing():
    import re
    from winidx.families import SPLIT_TEXT
    route = lambda parent, text: {s for s, p in SPLIT_TEXT[parent] if re.search(p, text)}
    assert route("MediaTek Wi-Fi", "mt7921_rz616_25.40.2.579") == {"MediaTek Wi-Fi 6E"}
    assert route("MediaTek Bluetooth", "mtk mt7925 bluetooth - 11") == {"MediaTek Bluetooth (Wi-Fi 7)"}
    assert route("MediaTek Wi-Fi", "mediatek wlan driver - 11") == set()
