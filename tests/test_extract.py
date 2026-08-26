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
