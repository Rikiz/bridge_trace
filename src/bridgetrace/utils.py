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
    """Check if text looks like a file path (contains path separators or drive letter).

    Excludes URI-style API paths that start with / and contain path parameters.
    """
    if len(text) >= 3 and text[1] == ":" and text[0].isalpha() and text[2] in ("\\", "/"):
        return True
    if "://" in text:
        return False
    if "\\" in text or "/" in text:
        return not (text.startswith("/") and _looks_like_api_path(text))
    return bool(text.startswith("./") or text.startswith("../") or text.startswith("~/"))


_PARAM_INDICATORS = frozenset({"{", "${"})


def _looks_like_api_path(text: str) -> bool:
    """Heuristic: does this /-prefixed string look like an API URI, not a file path?"""
    if "{" in text:
        return True
    if _has_file_extension(text):
        return False
    segments = [s for s in text.split("/") if s]
    if len(segments) <= 5 and not _has_file_extension(text):
        parts = text.rstrip("/").split("/")
        last = parts[-1] if parts else ""
        return not ("." in last and not last.startswith("."))
    return False


_FILE_EXTENSIONS = frozenset(
    {
        ".py",
        ".java",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".properties",
        ".conf",
        ".cfg",
        ".txt",
        ".md",
        ".rst",
        ".csv",
        ".class",
        ".jar",
        ".war",
        ".zip",
        ".tar",
        ".gz",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".sql",
        ".sh",
        ".bat",
        ".cmd",
        ".ps1",
        ".html",
        ".css",
        ".scss",
        ".less",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".toml",
        ".ini",
        ".env",
        ".lock",
    }
)


def _has_file_extension(text: str) -> bool:
    """Check if text ends with a known file extension."""
    lower = text.rsplit("/", 1)[-1].lower()
    dot = lower.rfind(".")
    if dot < 0:
        return False
    return lower[dot:] in _FILE_EXTENSIONS


def sanitize_for_id(text: str) -> str:
    """Normalize text for consistent ID generation across platforms."""
    if is_path_like(text):
        return normalize_path(text, strict=False)
    return text
