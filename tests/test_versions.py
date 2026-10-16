"""Tag normalization and comparison used by every upstream check."""
from core.versions import is_newer, is_prerelease, normalize_tag


def test_normalize_strips_v_prefix():
    assert normalize_tag("v0.38.2", "v") == "0.38.2"


def test_normalize_strips_custom_prefix():
    assert normalize_tag("open-design-v0.24.1", "open-design-v") == "0.24.1"


def test_normalize_requires_the_configured_prefix():
    assert normalize_tag("0.38.2", "open-design-v") is None


def test_normalize_rejects_foreign_tag():
    # vercel-labs/agent-browser style repo with an unrelated tag mixed in
    assert normalize_tag("mattpocock-skills@1.0.0", "v") is None


def test_normalize_rejects_non_version():
    assert normalize_tag("pr-3706-verification-assets", "v") is None


def test_normalize_keeps_prerelease_suffix():
    assert normalize_tag("v1.0.0-rc.1", "v") == "1.0.0-rc.1"


def test_is_newer_numeric_ordering():
    assert is_newer("0.38.2", "0.39.0") is True
    assert is_newer("0.39.0", "0.38.2") is False


def test_is_newer_equal_versions():
    assert is_newer("1.7.1", "1.7.1") is False


def test_is_newer_pads_short_versions():
    assert is_newer("1.7", "1.7.1") is True


def test_is_prerelease_flags_every_marked_shape():
    assert is_prerelease("1.0.0-rc.1") is True
    assert is_prerelease("0.0.2-beta.1") is True
    assert is_prerelease("2.0.0alpha3") is True


def test_is_prerelease_leaves_plain_releases_alone():
    assert is_prerelease("0.38.2") is False
    assert is_prerelease("1.7") is False
