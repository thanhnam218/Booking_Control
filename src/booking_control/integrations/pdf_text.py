"""Lay chu tu PDF dinh kem (booking confirmation) - pdfplumber (thuan Python, chay tren Windows).

PDF scan (anh chup, khong co lop chu) -> NO_TEXT: dua vao hang cho duyet, khong doan.
"""

from __future__ import annotations

import io


def extract_text(content: bytes, max_pages: int = 6) -> tuple[str, str, str]:
    """Tra (text, status, loi). status: OK | NO_TEXT | FAILED | SKIPPED."""
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return "", "SKIPPED", "Chua cai pdfplumber (pip install pdfplumber)"
    try:
        parts = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages[:max_pages]:
                parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
        return (text, "OK", "") if text else ("", "NO_TEXT", "PDF khong co lop chu (anh scan)")
    except Exception as exc:  # noqa: BLE001
        return "", "FAILED", f"{type(exc).__name__}: {exc}"
