import json

from winidx import db, hwids, publish
from winidx.vendors.silicon import parse_chipset_rn

RN = """<table><tr><th></th><th>Windows 10</th><th>Windows 11</th><th>Change Details</th></tr>
<tr><td>AMD GPIO2 Driver</td><td>2.2.0.137</td><td>2.2.0.137</td><td>Bug Fixes</td></tr>
<tr><td>PT GPIO Driver</td><td>3.0.3.0</td><td>3.0.5.0</td><td>No change</td></tr>
<tr><td>AMD IOV Driver</td><td>1.2.0.52</td><td>Not Applicable</td><td>No change</td></tr>
<tr><td>AMD PMF Driver</td><td>Not Applicable</td><td>26.10.15.0</td><td>New</td></tr>
</table>"""


def test_parse_chipset_rn_uses_windows_11_column():
    rows = {(inf, ver) for _t, ver, inf, _x in parse_chipset_rn(RN)}
    # PMF has no mapped INF; IOV is not applicable on Windows 11
    assert rows == {("amdgpio2.inf", "2.2.0.137"), ("amdgpio3.inf", "3.0.5.0")}


def test_vendor_class_ids():
    toks = ["PCI\\VEN_1002&CC_0403", "PCI\\VEN_1002&CC_040300", "PCI\\CC_0403",
            "PCI\\VEN_1002&DEV_1640"]
    assert hwids.specific(toks) == ["PCI\\VEN_1002&DEV_1640"]
    assert hwids.vendor_class(toks) == ["PCI\\VEN_1002&CC_0403",
                                        "PCI\\VEN_1002&CC_040300"]


def test_merge_upstream_infs(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    vendor_row = {
        "inf_name": "realtekservice.inf", "inf_series": "realtekservice.inf",
        "driver_ver": "1.0.1018.0", "driver_ver_aliases": [],
        "driver_ver_normalised": [1, 0, 1018, 0], "driver_date": "2026-07-14",
        "class": "SoftwareComponent", "family_ids": {7}, "artefact_ids": {1},
        "hwids": {"SWC\\VEN_10EC&SID_0001"}, "first_published": "2026-07-20",
        "sources": {"vendor"}}
    groups = {("realtekservice.inf", "1.0.1018.0"): vendor_row}
    for ver in ("1.0.1018.0", "1.0.1033.0"):
        db.upsert_upstream_inf(conn, "2026-09-29", source="wucatalog",
                               inf_name="realtekservice.inf", driver_ver=ver,
                               published="2026-08-17",
                               hwids=["SWC\\VEN_10EC&SID_0001"])
    publish._merge_upstream_infs(conn, groups)
    assert vendor_row["sources"] == {"vendor", "wucatalog"}
    new = groups[("realtekservice.inf", "1.0.1033.0")]
    assert new["sources"] == {"wucatalog"}
    assert new["artefact_ids"] == set() and new["driver_date"] is None
    assert new["family_ids"] == {7} and new["class"] == "SoftwareComponent"
    assert new["first_published"] == "2026-08-17"
    assert json.dumps(sorted(new["hwids"])) == '["SWC\\\\VEN_10EC&SID_0001"]'


def test_parse_realtek_lan_picks_power_saving_netadaptercx():
    from winidx.vendors.silicon import parse_realtek_lan
    body = """<table>
<tr><td><a href="x"></a></td><td>Win10/Win11 Auto Installation Program (NDIS)</td><td>10.80.50</td><td>2026/08/28</td><td>5 MB</td></tr>
<tr><td><a href="x"></a></td><td>Win11 Auto Installation Program (NetAdapterCx)</td><td>11.031.50</td><td>2026/08/28</td><td>3 MB</td></tr>
<tr><td><a href="x"></a></td><td>Win11 Auto Installation Program (NetAdapterCx) - Not Support Power Saving</td><td>11.031.20</td><td>2026/08/28</td><td>3 MB</td></tr>
</table>"""
    assert parse_realtek_lan(body) == ("11.031.50", "2026-08-28")
