"""Shared fixtures.

`write_pdf` builds a minimal one-page PDF from literal lines, so quote tests are
hermetic: no copyrighted paper is committed, and the PDF-extraction artifacts
under test (line breaks, hyphenation, punctuation spacing) are reproduced
exactly and on purpose rather than hoped for.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _minimal_pdf(lines: list[str]) -> bytes:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text = ("BT /F1 11 Tf 14 TL 50 760 Td\n"
            + "\n".join(f"({esc(line)}) Tj T*" for line in lines)
            + "\nET")
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n")
    return out.encode("latin-1")


@pytest.fixture
def write_pdf(tmp_path):
    """Write a one-page PDF containing `lines`; return its path."""
    def _write(lines: list[str], name: str = "paper.pdf") -> Path:
        path = tmp_path / name
        path.write_bytes(_minimal_pdf(lines))
        return path
    return _write
