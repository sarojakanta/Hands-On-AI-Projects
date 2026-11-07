"""Unit tests for filename validation and secret masking."""

from __future__ import annotations

import pytest

from app.core.security import (
    InvalidFilenameError,
    mask_secret,
    resolve_inside,
    validate_filename,
)

TXT = {".txt"}


@pytest.mark.parametrize(
    "name",
    ["notes.txt", "error reference.txt", "profit_and_loss.txt", "NOTES.TXT", "report (v2).txt"],
)
def test_validate_filename_accepts_plain_names(name):
    assert validate_filename(name, TXT) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "..\\evil.txt",
        "../evil.txt",
        "sub/evil.txt",
        "sub\\evil.txt",
        "C:\\evil.txt",
        "evil.txt:stream",
        "..",
        "....txt",
        ".hidden.txt",
        "CON.txt",
        "nul.txt",
        "bad\x00name.txt",
        "tab\tname.txt",
        "evil.exe",
        "evil.txt.exe",
        "noextension",
        "a" * 300 + ".txt",
    ],
)
def test_validate_filename_rejects_unsafe_names(name):
    with pytest.raises(InvalidFilenameError):
        validate_filename(name, TXT)


def test_resolve_inside_accepts_direct_child(tmp_path):
    assert resolve_inside(tmp_path, "a.txt") == (tmp_path / "a.txt").resolve()


@pytest.mark.parametrize("name", ["../a.txt", "..\\a.txt", "sub/../../a.txt"])
def test_resolve_inside_rejects_escape(tmp_path, name):
    (tmp_path / "docs").mkdir()
    with pytest.raises(InvalidFilenameError):
        resolve_inside(tmp_path / "docs", name)


def test_mask_secret_never_returns_full_value():
    secret = "sk-1234567890abcdef"
    masked = mask_secret(secret)
    assert secret not in masked
    assert masked.endswith("cdef")
    assert mask_secret(None) == ""
    assert mask_secret("short") == "*****"
