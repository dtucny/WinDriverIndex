from winidx import scope


def test_am5_with_suffix():
    assert scope.extract_chipset("B650E EAGLE") == ("B650E", "AM5")


def test_intel():
    assert scope.extract_chipset("Z790 AORUS ELITE AX") == ("Z790", "LGA1700")


def test_out_of_scope_legacy():
    assert scope.extract_chipset("GA-Z270X-Gaming 5") is None
    assert scope.extract_chipset("GA-386PS") is None


def test_am4():
    assert scope.extract_chipset("MAG B550 TOMAHAWK") == ("B550", "AM4")


def test_form_factor_suffixes_match_but_are_stripped():
    # regression: '(E|A)?\b' silently rejected every M/I/TM-suffixed name
    assert scope.extract_chipset("B550M PRO-VDH WIFI") == ("B550", "AM4")
    assert scope.extract_chipset("A620M GAMING") == ("A620", "AM5")
    assert scope.extract_chipset("MAG B650M MORTAR WIFI") == ("B650", "AM5")
    assert scope.extract_chipset("B860TM-ITX/TPM/TB4/DP") == ("B860", "LGA1851")


def test_e_variant_still_kept():
    assert scope.extract_chipset("B650E EAGLE") == ("B650E", "AM5")
    assert scope.extract_chipset("X870E AORUS MASTER") == ("X870E", "AM5")
