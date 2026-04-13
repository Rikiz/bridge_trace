"""Cross-platform utilities for BridgeTrace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union


def normalize_path(path: Union[str, Path], strict: bool = False) -> str:
    """Convert any absolute path to POSIX style for consistent IDs across platforms.

    Args:
        path: Path string or Path object (absolute or relative)
        strict: If True, require path to be absolute or resolvable; if False,
                return the input untouched if resolution fails
    Returns:
        POSIX-style path string (forward slashes)
    """
    # Convert to string and replace backslashes with forward slashes
    path_str = str(path)

    # First, normalize separators
    normalized = path_str.replace("\\", "/")

    # Check if it looks like a Windows absolute path (e.g., C:/Users)
    # Pattern: A:/ or A:\ (already normalized to A:/)
    is_windows_abs = (
        len(normalized) >= 3
        and normalized[0].isalpha()
        and normalized[1] == ":"
        and normalized[2] == "/"
    )

    if is_windows_abs:
        # Windows absolute path: keep as-is (already normalized separators)
        # No resolution needed as it's already absolute
        return normalized

    # For other paths, try to resolve to absolute path if possible
    try:
        p = Path(normalized)
        # For relative paths, resolve() will make them absolute relative to cwd
        resolved = p.resolve()
        return resolved.as_posix()
    except (OSError, RuntimeError):
        # Resolution failed (e.g., path contains invalid characters, symlink loop)
        if strict:
            raise
        # Return with normalized separators
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
    if text.startswith("./") or text.startswith("../") or text.startswith("~/"):
        return True
    return False


def sanitize_for_id(text: str) -> str:
    """Normalize text for consistent ID generation across platforms."""
    if is_path_like(text):
        return normalize_path(text, strict=False)
    return text
