"""Pure helpers for parsing THIRD_PARTY_NOTICES.txt into titled sections."""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["LicenseSection", "parse_license_sections"]

_SECTION_SEPARATOR = re.compile(r"^[ \t]*----[ \t]*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class LicenseSection:
    title: str
    body: str


def parse_license_sections(notices: str) -> tuple[LicenseSection, ...]:
    """Split notices text into sections separated by ---- divider lines."""
    sections: list[LicenseSection] = []
    for block in _SECTION_SEPARATOR.split(notices):
        block = block.strip("\n")
        if not block.strip():
            continue
        title, _, body = block.partition("\n")
        sections.append(LicenseSection(title=title.strip(), body=body.strip("\n")))
    return tuple(sections)
