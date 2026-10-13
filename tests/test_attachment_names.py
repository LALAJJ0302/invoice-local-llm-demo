"""An attachment's name is chosen by whoever sent the email, so it must not choose the folder.

Before this file, `safe_filename` removed only `/` and `\\`. On Windows `inbox / "C:evil.pdf"`
is the drive-relative path `C:evil.pdf`, outside inbox/ entirely (codeql-fixes-spec.md, SA-02).
Kept separate from `test_email_listener.py`, which exists on another branch.
"""
from pathlib import Path

import pytest

from email_listener import _is_inside, safe_filename

HOSTILE = [
    "C:evil.pdf",
    "=?utf-8?b?Qzpldmlslpdf?=",          # encoded word for "C:evil" + junk
    "invoice.pdf:hidden.pdf",            # NTFS alternate data stream
    "..",
    "../../x.pdf",
    "..\\..\\x.pdf",
    "C:\\Windows\\x.pdf",
    "/etc/passwd.pdf",
    "\\\\server\\share\\x.pdf",
    "a\x00b.pdf",
    "",
    None,
    "   ",
]


@pytest.mark.parametrize("raw", HOSTILE)
def test_a_hostile_name_stays_inside_the_folder(tmp_path, raw):
    name = safe_filename(raw)
    assert name
    assert _is_inside(tmp_path, tmp_path / name)


@pytest.mark.parametrize("raw", HOSTILE)
def test_a_hostile_name_has_no_separator_or_drive_left(raw):
    name = safe_filename(raw)
    assert not set(name) & set('<>:"/\\|?*')
    assert name not in (".", "..")
    assert not any(ord(ch) < 32 for ch in name)


@pytest.mark.parametrize("raw", [
    "Invoice 2024-001.pdf",
    "invoice.v2.pdf",
    "發票-001.pdf",
    "Rechnung_März.pdf",
])
def test_an_ordinary_name_is_unchanged(raw):
    assert safe_filename(raw) == raw


@pytest.mark.parametrize("raw", ["CON.pdf", "nul.txt", "COM1.pdf", "lpt9.csv"])
def test_a_reserved_device_name_is_renamed_and_keeps_its_extension(raw):
    name = safe_filename(raw)
    assert name != raw
    assert Path(name).suffix == Path(raw).suffix
    assert Path(name).stem.split(".")[0].upper() not in {"CON", "NUL", "COM1", "LPT9"}


def test_a_trailing_dot_or_space_is_removed():
    assert safe_filename("invoice.pdf. ") == "invoice.pdf"


def test_an_over_long_name_is_capped_and_keeps_its_extension():
    name = safe_filename("a" * 500 + ".pdf")
    assert len(name) <= 200
    assert name.endswith(".pdf")


def test_the_drive_letter_is_dropped_not_kept_as_a_prefix():
    assert safe_filename("C:evil.pdf") == "evil.pdf"


def test_is_inside_rejects_a_path_that_escapes(tmp_path):
    assert _is_inside(tmp_path, tmp_path / "invoice.pdf")
    assert not _is_inside(tmp_path, tmp_path / ".." / "invoice.pdf")
    assert not _is_inside(tmp_path, tmp_path)
