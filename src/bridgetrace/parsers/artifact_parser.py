"""Artifact inspector for Java .class files.

Uses ``javap -v`` to extract annotation path information from compiled classes.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bridgetrace.models.graph import ParseResult, URIMatch
from bridgetrace.parsers.base import BaseParser

_ANNOTATION_PATH_RE = re.compile(
    r'"(\/(?:[\w\-\.]+\/)+[\w\-\.]*)"'
)  # same pattern as URI_PATH_RE but quoted


class ArtifactParser(BaseParser):
    """Inspect Java .class files via javap for annotation path info."""

    supported_extensions = (".class",)

    def parse(self, path: Path) -> ParseResult:
        """Run javap -v on a .class file and extract annotation path strings."""
        uris = self._extract_annotation_uris(path)
        return ParseResult(file_path=str(path), uris=uris, functions=[], calls=[])

    def _extract_annotation_uris(self, path: Path) -> list[URIMatch]:
        """Call javap -v and parse annotation strings for URI paths."""
        try:
            result = subprocess.run(
                ["javap", "-v", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

        if result.returncode != 0:
            return []

        matches: list[URIMatch] = []
        seen: set[str] = set()

        for line in result.stdout.splitlines():
            if "RuntimeVisibleAnnotations" not in line and "@" not in line:
                continue
            for m in _ANNOTATION_PATH_RE.finditer(line):
                uri = m.group(1)
                if uri not in seen:
                    seen.add(uri)
                    matches.append(URIMatch(uri=uri, source_file=str(path)))

        return matches
