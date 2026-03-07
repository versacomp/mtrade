"""Unit tests for version.py."""

import version


def test_version_is_string():
    assert isinstance(version.__version__, str)


def test_version_format():
    """Version follows semantic versioning: MAJOR.MINOR.PATCH."""
    parts = version.__version__.split(".")
    assert len(parts) == 3
    for part in parts:
        assert part.isdigit(), f"Expected numeric version part, got {part!r}"


def test_version_value():
    assert version.__version__ == "0.0.1"
