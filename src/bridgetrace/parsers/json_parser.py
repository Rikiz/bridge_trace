"""Recursive JSON/YAML value-centric URI detector.

Ignores keys — extracts only string values that match a URI path pattern.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from bridgetrace.models.graph import ParseResult, URIMatch
from bridgetrace.parsers.base import BaseParser
from bridgetrace.utils import normalize_path

URI_PATH_RE = re.compile(r"^/(?:[\w\-\.]+/)+[\w\-\.]*$")


def _walk_values(obj: Any, source_path: str) -> list[URIMatch]:
    """Recursively walk a mapping/sequence and collect URI-like string values."""
    matches: list[URIMatch] = []

    if isinstance(obj, dict):
        for v in obj.values():
            matches.extend(_walk_values(v, source_path))
    elif isinstance(obj, list):
        for item in obj:
            matches.extend(_walk_values(item, source_path))
    elif isinstance(obj, str) and URI_PATH_RE.match(obj):
        matches.append(URIMatch(uri=obj, source_file=source_path))

    return matches


class JsonYamlParser(BaseParser):
    """Value-centric JSON/YAML parser that extracts URI paths from string values."""

    supported_extensions = (".json", ".yaml", ".yml")

    def parse(self, path: Path) -> ParseResult:
        """Parse a JSON/YAML file and extract all URI-like string values."""
        text = path.read_text(encoding="utf-8", errors="replace")
        data: Any = None

        # Normalize path for cross-platform consistency
        normalized_path = normalize_path(path)

        if path.suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return ParseResult(file_path=normalized_path, uris=[], functions=[], calls=[])
        else:
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError:
                return ParseResult(file_path=normalized_path, uris=[], functions=[], calls=[])

        if data is None:
            return ParseResult(file_path=normalized_path, uris=[], functions=[], calls=[])

        uri_matches = _walk_values(data, normalized_path)
        return ParseResult(
            file_path=normalized_path,
            uris=uri_matches,
            functions=[],
            calls=[],
        )
