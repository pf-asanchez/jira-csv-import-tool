from __future__ import annotations

from pathlib import Path

TEXT_FILE_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


def read_text_with_fallback(path: Path) -> str:
    raw_bytes = path.read_bytes()
    for encoding in TEXT_FILE_ENCODINGS:
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue

    attempted = ", ".join(TEXT_FILE_ENCODINGS)
    raise UnicodeDecodeError(
        "utf-8",
        raw_bytes,
        0,
        1,
        f"Could not decode {path} using supported encodings: {attempted}",
    )
