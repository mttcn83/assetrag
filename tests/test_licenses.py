import pytest
from assetrag import licenses
from conftest import MIT, NC

def test_detect_mit_and_nc():
    assert licenses.detect(MIT) == "MIT"
    assert licenses.detect(NC) is None

def test_unknown_and_missing_rejected():
    assert not licenses.check(None).ok
    assert not licenses.check("Proprietary").ok
    assert "non-commercial" in licenses.check("CC-BY-NC-4.0").reason

@pytest.mark.parametrize("raw,expected", [("MIT OR Apache-2.0", "MIT"), ("GPL-3.0", "GPL-3.0-only"),
                                          ("Apache-2.0 WITH LLVM-exception", None), ("MIT AND GPL-3.0-only", "GPL-3.0-only")])
def test_normalize(raw, expected):
    assert licenses.normalize_spdx(raw) == expected

def test_copyleft_by_profile():
    assert not licenses.check("GPL-3.0-only", "closed_source").ok
    assert licenses.check("GPL-3.0-only", "open_source").ok
    assert not licenses.check("AGPL-3.0-only", "saas").ok
    assert licenses.check("MPL-2.0", "closed_source").ok

def test_notices_prohibited_excludes_attribution_licenses():
    assert not licenses.check("MIT", allow_notices=False).ok
    assert licenses.check("CC0-1.0", allow_notices=False).ok

def test_dependency_license_blocks():
    v = licenses.check("MIT", "closed_source", dep_licenses={"x": "GPL-3.0-only"})
    assert not v.ok and "dependency x" in v.reason
    assert not licenses.check("MIT", dep_licenses={"y": "UNKNOWN"}).ok

def test_isc_first_license_wins():
    t = "ISC License\n\nPermission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby granted, provided that the above copyright notice and this permission notice appear in all copies.\n\n" + MIT
    assert licenses.detect(t) == "ISC"


def test_mentioned_licenses_do_not_override_the_granted_one():
    mpl = ("Mozilla Public License Version 2.0\n1. Definitions\n1.12. Secondary License means either the GNU General Public License, "
           "Version 2.0, the GNU Lesser General Public License, Version 2.1, the GNU Affero General Public License, Version 3.0, "
           "or any later versions of those licenses.\n")
    assert licenses.detect(mpl) == "MPL-2.0"
    gpl3 = ("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n...\n13. Use with the GNU Affero General Public License. "
            "Notwithstanding any other provision of this License, version 3 of the GNU Affero General Public License ...")
    assert licenses.detect(gpl3) == "GPL-3.0-only"
