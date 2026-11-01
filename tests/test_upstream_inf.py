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


def test_catalog_throttle_retries_and_never_returns_refusal(monkeypatch, tmp_path):
    import pytest
    from winidx.vendors import wucatalog as w
    monkeypatch.setattr(w, "THROTTLE_BACKOFF", (0, 0))
    monkeypatch.setattr(w.time, "sleep", lambda s: None)

    class Resp:
        def __init__(self, text): self.content = text.encode()

    class Client:
        snapshot_dir = tmp_path
        def __init__(self, bodies): self.bodies = list(bodies)
        def get(self, url, params=None, snapshot=None):
            (tmp_path / snapshot).write_text(self.bodies[0])
            return Resp(self.bodies.pop(0))

    refusal = "<p>Too many search requests. Please wait a few minutes</p>"
    assert w._search(Client([refusal, "<table>ok</table>"]), "PCI\\VEN_1") == "<table>ok</table>"
    with pytest.raises(w.CatalogThrottled):
        w._search(Client([refusal] * 3), "PCI\\VEN_2")
    assert not (tmp_path / "wu_PCI_VEN_2.html").exists()   # refusal not kept


def test_silicon_installer_link_follows_listed_version():
    from winidx.vendors.silicon import DOWNLOADS, download_link
    amd = ('<a href="https://drivers.amd.com/drivers/whql-amd-software-adrenalin-edition-'
           '26.10.1-win11-a.exe">x</a> <a href="https://drivers.amd.com/drivers/installer/'
           '26.20/beta/amd-software-adrenalin-edition-26.10.2-minimalsetup-261029_web.exe">'
           '</a> <a href="https://drivers.amd.com/drivers/whql-amd-software-adrenalin-'
           'edition-26.10.2-win11-a.exe">')
    (_, a), (page_b, b) = DOWNLOADS["AMD Graphics"]
    assert download_link(a, amd, "26.10.2").endswith("26.10.2-win11-a.exe")
    assert download_link(b, amd, "26.10.2") is None and page_b
    nv = '{"Version" : "621.01", "DownloadURL" : "https://us.download.nvidia.com/Windows/621.01/621.01-desktop-win10-win11-64bit-international-dch-whql.exe"}'
    ((_, pat),) = DOWNLOADS["NVIDIA Graphics"]
    assert download_link(pat, nv, "621.01").startswith("https://us.download")
    assert download_link(pat, nv, "616.92") is None


def test_silicon_artefact_links_a_page():
    from winidx.vendors.silicon import PAGES, page_link
    url = dict((f, u) for f, u, _ in PAGES)
    nv = '{"DetailsURL" : "https:\\/\\/www.nvidia.com\\/en-us\\/drivers\\/details\\/282412\\/"}'
    assert (page_link("NVIDIA Graphics", url["NVIDIA Graphics"], nv)
            == "https://www.nvidia.com/en-us/drivers/details/282412/")
    assert page_link("NVIDIA Graphics", url["NVIDIA Graphics"], "{}") == url["NVIDIA Graphics"]
    assert "7800x3d" not in page_link("AMD Graphics", url["AMD Graphics"], "")
    assert page_link("Intel Wi-Fi", url["Intel Wi-Fi"], "") == url["Intel Wi-Fi"]


def test_silicon_payload_infs_are_tagged_silicon(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    fid = conn.execute("INSERT INTO family (name, silicon_vendor, component)"
                       " VALUES ('NVIDIA Graphics', 'nvidia', 'graphics')").lastrowid
    db.upsert_upstream_payload(conn, "2026-11-01", family="NVIDIA Graphics",
                               version="621.01", url="https://x/621.01.exe",
                               published="2026-10-21")
    conn.execute("UPDATE upstream_payload SET sha256 = 'p1'")
    conn.execute("INSERT INTO inf VALUES ('p1', 'Display.Driver/nv_dispsig.inf', 'i1',"
                 " 'NVIDIA', 'Display', '2026-10-17', '32.0.16.2101',"
                 " '[\"PCI\\\\VEN_10DE&DEV_2782\"]')")
    (row,) = publish._infs(conn)
    assert row["sources"] == ["silicon"] and row["artefact_ids"] == []
    assert row["family_ids"] == [fid] and row["first_published"] == "2026-10-21"
    # a later crawl that links the same installer keeps its hash
    db.upsert_upstream_payload(conn, "2026-11-02", family="NVIDIA Graphics",
                               version="621.01", url="https://x/621.01.exe",
                               published=None)
    r = conn.execute("SELECT sha256, published, last_seen FROM upstream_payload").fetchone()
    assert tuple(r) == ("p1", "2026-10-21", "2026-11-02")


def test_silicon_row_keeps_earlier_upstream_date(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_upstream_payload(conn, "2026-11-01", family="AMD Graphics",
                               version="32.0.32015.12012", url="https://x/a.exe",
                               published="2026-10-30")
    conn.execute("UPDATE upstream_payload SET sha256 = 'p1'")
    conn.execute("INSERT INTO inf VALUES ('p1', 'amdfdans/AMDFDANS.inf', 'i1', 'AMD',"
                 " 'SoftwareComponent', '2026-06-18', '1.4.41.0',"
                 " '[\"SWC\\\\VEN_1002&AID_0001\"]')")
    db.upsert_upstream_inf(conn, "2026-10-31", source="wucatalog", inf_name="amdfdans.inf",
                           driver_ver="1.4.41.0", published="2026-06-17",
                           hwids=["SWC\\VEN_1002&AID_0001"])
    (row,) = publish._infs(conn)
    assert row["sources"] == ["silicon", "wucatalog"]
    assert row["first_published"] == "2026-06-17" and row["driver_date"] == "2026-06-18"


def test_realtek_build_comes_from_the_stored_installer(tmp_path):
    from winidx.vendors.silicon import _realtek_row, _RTK_NDIS, _stored_inf_ver
    body = ('<table><tr><td><a href="/Download/ToDownload?type=direct&amp;downloadid=3613">'
            '</a></td><td>Win10/Win11 Auto Installation Program (NDIS)</td><td>10.80.50</td>'
            '<td>2026/10/05</td><td>5 MB</td></tr></table>')
    ver, date, url = _realtek_row(body, _RTK_NDIS)
    assert (ver, date) == ("10.80.50", "2026-10-05") and url.endswith("downloadid=3613")
    conn = db.connect(tmp_path / "t.sqlite")
    assert _stored_inf_ver(conn, url, ver, "rt640x64.inf") is None
    db.upsert_upstream_payload(conn, "2026-11-01", family="Realtek 8125 LAN",
                               version="10.80.50", url=url, published=date)
    conn.execute("UPDATE upstream_payload SET sha256 = 'p1'")
    conn.execute("INSERT INTO inf VALUES ('p1', '!Install/WIN11/64/rt640x64.inf', 'i1',"
                 " 'Realtek', 'Net', '2026-04-07', '10.080.50.0407', '[]')")
    assert _stored_inf_ver(conn, url, ver, "rt640x64.inf") == "10.080.50.0407"
    # a newer release on the page than the stored installer: no build to give
    assert _stored_inf_ver(conn, url, "10.80.51", "rt640x64.inf") is None
