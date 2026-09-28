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
    got = _water_lines({"version_normalised": [32, 0, 31041, 1004]}, fam_lines)
    assert [x["major"] for x in got] == [32, 25, 21]
    assert got[0]["water_line"] and not got[0]["parallel_to_water"]
    assert got[1]["parallel_to_water"] and not got[2]["parallel_to_water"]


def test_msi_board_code():
    assert _MSI_CODE.match("7C96v1L9").group(1) == "7C96"
    assert _MSI_CODE.match("7E15") is None
