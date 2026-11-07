from __future__ import annotations

from puripuly_heart.ui.about.licenses import LicenseSection, parse_license_sections
from tests.helpers.paths import SOURCE_ROOT


def test_parse_license_sections_splits_on_dashes() -> None:
    text = "Title A\nbody A\n\n----\n\nTitle B\nbody B\n"
    assert parse_license_sections(text) == (
        LicenseSection("Title A", "body A"),
        LicenseSection("Title B", "body B"),
    )


def test_parse_license_sections_handles_empty_and_single_block() -> None:
    assert parse_license_sections("") == ()
    assert parse_license_sections("Only title\n") == (LicenseSection("Only title", ""),)


def test_parse_license_sections_real_notices_have_titles() -> None:
    text = (SOURCE_ROOT / "data" / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    sections = parse_license_sections(text)
    assert len(sections) == 24
    assert sections[0].title == "PuriPuly Heart license and third-party notices"
    titles = [section.title for section in sections]
    assert "MIT License" in titles
    assert all(titles)
