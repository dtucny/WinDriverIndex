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
