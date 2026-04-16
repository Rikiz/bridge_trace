"""Artifact inspector for Java .class files.

Uses ``javap -v`` to extract annotation path information from compiled classes.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bridgetrace.models.graph import EndpointImpl, ParseResult, URIMatch
from bridgetrace.parsers.base import BaseParser
from bridgetrace.parsers.json_parser import URI_PATH_RE
from bridgetrace.utils import normalize_path

_QUOTED_STRING_RE = re.compile(r'"(\/[^"]+)"')

_ANNOTATION_METHOD_MAP: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": "",
}

_ANNOTATION_RE = re.compile(r"@(\w+Mapping)")

_CLASS_DECL_RE = re.compile(r"^class\s+(\S+)")
_METHOD_DECL_RE = re.compile(r"^\s+(?:public|private|protected|static|\s)+(\w+)\s*\(")


class ArtifactParser(BaseParser):
    """Inspect Java .class files via javap for annotation path info."""

    supported_extensions = (".class",)

    def parse(self, path: Path) -> ParseResult:
        """Run javap -v on a .class file and extract annotation path strings."""
        normalized_path = normalize_path(path)
        uris, endpoint_impls = self._extract_annotation_uris(path, normalized_path)
        return ParseResult(
            file_path=normalized_path,
            uris=uris,
            functions=[],
            calls=[],
            endpoint_impls=endpoint_impls,
        )

    def _extract_annotation_uris(
        self, path: Path, normalized_path: str
    ) -> tuple[list[URIMatch], list[EndpointImpl]]:
        """Call javap -v and parse annotation strings for URI paths."""
        try:
            result = subprocess.run(
                ["javap", "-v", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return [], []

        if result.returncode != 0:
            return [], []

        uri_matches: list[URIMatch] = []
        endpoint_impls: list[EndpointImpl] = []
        seen_uris: set[str] = set()

        current_class: str = ""
        current_method: str = ""
        current_line: int = 0
        current_http_method: str = ""

        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            class_m = _CLASS_DECL_RE.match(line)
            if class_m:
                current_class = class_m.group(1).rsplit(".", 1)[-1]
                continue

            method_m = _METHOD_DECL_RE.match(line)
            if method_m:
                current_method = method_m.group(1)
                current_line = i + 1
                current_http_method = ""
                continue

            if "RuntimeVisibleAnnotations" not in line and "@" not in line:
                continue

            ann_m = _ANNOTATION_RE.search(line)
            if ann_m:
                current_http_method = _ANNOTATION_METHOD_MAP.get(ann_m.group(1), "")

            for m in _QUOTED_STRING_RE.finditer(line):
                uri = m.group(1)
                if not URI_PATH_RE.match(uri):
                    continue
                if uri not in seen_uris:
                    seen_uris.add(uri)
                    uri_matches.append(
                        URIMatch(uri=uri, source_file=normalized_path, role="implementation")
                    )
                if current_method:
                    func_name = (
                        f"{current_class}.{current_method}" if current_class else current_method
                    )
                    endpoint_impls.append(
                        EndpointImpl(
                            uri=uri,
                            function_name=func_name,
                            function_line=current_line,
                            http_method=current_http_method,
                        )
                    )
                current_http_method = ""

        return uri_matches, endpoint_impls
