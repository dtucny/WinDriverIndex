from winidx import versions


def test_zero_padding_equivalence():
    # MSI vs ASUS packaging of the same driver (spec §6.3)
    assert versions.parse("3.05.00.1380").tuple == versions.parse("3.5.0.1380").tuple


def test_vendor_prefix():
    assert versions.parse("XB560NF_v6001.16.175.0").tuple == (6001, 16, 175, 0)


def test_suffix_retained_numerically():
    assert versions.parse("6.0.9520.1_Nahimic").tuple == (6, 0, 9520, 1)


def test_messy_realtek():
    assert versions.parse("9977.1_UAD_WHQL").tuple == (9977, 1)


def test_beta_marker():
    v = versions.parse("7.05.06.113 (Beta version)")
    assert v.is_beta and v.tuple == (7, 5, 6, 113)


def test_unparseable_falls_back():
    v = versions.parse("F32a")
    assert v.tuple is None and v.normalised_json is None


def test_ordering():
    old = versions.parse("3.4.0.1063")
    new = versions.parse("3.05.00.1380")
    assert versions.compare_key(new) > versions.compare_key(old)


def test_unparseable_sorts_lowest():
    assert versions.compare_key(versions.parse("F32a")) < versions.compare_key(versions.parse("0.1"))


def test_nvidia_inf_to_marketing():
    from winidx.publish import _nv_marketing
    assert _nv_marketing("32.0.15.9186") == "591.86"
    assert _nv_marketing("31.0.15.3623") == "536.23"
    assert _nv_marketing("32.0.16.1074") == "610.74"
    assert _nv_marketing("616.56") is None          # already marketing
    assert _nv_marketing("32.0.101.8991") is None   # Intel scheme
    assert _nv_marketing(None) is None


def test_realtek_uad_to_canonical():
    from winidx.publish import _rtk_uad
    assert _rtk_uad("10007.1_UAD_WHQL") == "6.0.10007.1"
    assert _rtk_uad("9679.1 UAD") == "6.0.9679.1"
    assert _rtk_uad("6.0.9679.1") is None
    assert _rtk_uad(None) is None
