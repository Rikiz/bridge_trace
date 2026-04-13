"""Cross-platform utilities for BridgeTrace."""

from __future__ import annotations

import os
from pathlib import Path


def normalize_path(path: str | Path, strict: bool = False) -> str:
    """Convert any absolute path to POSIX style for consistent IDs across platforms.

    Args:
        path: Path string or Path object (absolute or relative)
        strict: If True, require path to be absolute or resolvable; if False,
                return the input untouched if resolution fails
    Returns:
        POSIX-style path string (forward slashes)
    """
    path_str = str(path)

    normalized = path_str.replace("\\", "/")

    is_windows_abs = (
        len(normalized) >= 3
        and normalized[0].isalpha()
        and normalized[1] == ":"
        and normalized[2] == "/"
    )

    if is_windows_abs:
        if len(normalized) > 260 and os.name == "nt":
            normalized = "//?/" + normalized
        return normalized

    try:
        p = Path(normalized)
        resolved = p.resolve()
        result = resolved.as_posix()
        if len(result) > 260 and os.name == "nt" and result[1] == ":":
            result = "//?/" + result
        return result
    except (OSError, RuntimeError):
        if strict:
            raise
        return normalized


def is_path_like(text: str) -> bool:
    """Check if text looks like a file path (contains path separators or drive letter)."""
    # Check for Windows absolute path pattern: A:\ or A:/
    if len(text) >= 3 and text[1] == ":" and text[0].isalpha() and text[2] in ("\\", "/"):
        return True
    # Check for path separators (but exclude URLs with ://)
    if "://" in text:
        return False
    if "\\" in text or "/" in text:
        return True
    # Check for common path patterns
    return bool(text.startswith("./") or text.startswith("../") or text.startswith("~/"))


def sanitize_for_id(text: str) -> str:
    """Normalize text for consistent ID generation across platforms."""
    if is_path_like(text):
        return normalize_path(text, strict=False)
    return text
