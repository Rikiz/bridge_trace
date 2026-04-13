"""Core scanner: walks file trees, dispatches to parsers, builds graph data."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Sequence

import pathspec

from bridgetrace.config import settings
from bridgetrace.models.graph import (
    GraphEdge,
    GraphNode,
    ParseResult,
)
from bridgetrace.parsers.artifact_parser import ArtifactParser
from bridgetrace.parsers.base import BaseParser
from bridgetrace.parsers.json_parser import JsonYamlParser
from bridgetrace.parsers.treesitter_parser import TreeSitterParser
from bridgetrace.utils import normalize_path, sanitize_for_id

logger = logging.getLogger(__name__)

_SCAN_EXTENSIONS: set[str] = {
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".ts",
    ".tsx",
    ".java",
    ".class",
}


class Scanner:
    """Orchestrates file discovery, parser dispatch, and graph model construction."""

    def __init__(self, ignore_gitignore: bool | None = None) -> None:
        self._ignore_gitignore = (
            ignore_gitignore if ignore_gitignore is not None else settings.ignore_gitignore
        )
        self._parsers: list[BaseParser] = [
            JsonYamlParser(),
            TreeSitterParser(),
            ArtifactParser(),
        ]

    def scan_paths(self, roots: Sequence[Path]) -> list[ParseResult]:
        """Walk all roots and return aggregated parse results."""
        all_files = self._discover_files(roots)
        logger.info("Discovered %d files across %d roots", len(all_files), len(roots))

        results: list[ParseResult] = []
        for fpath in all_files:
            result = self._parse_file(fpath)
            if result is not None:
                results.append(result)
        return results

    def build_graph_entities(
        self, results: list[ParseResult], group_name: str, group_roots: Sequence[Path] = ()
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Convert parse results into Neo4j-ready nodes and edges.

        Args:
            results: Parse results from scan_paths.
            group_name: Logical group name.
            group_roots: The repository root paths bound to this group.
                         Used for longest-prefix matching to determine repo names.
        """
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        seen_repo_ids: set[str] = set()
        seen_file_ids: set[str] = set()
        seen_endpoint_edges: set[str] = set()
        seen_function_edges: set[str] = set()

        # Build sorted normalized roots for longest-prefix matching (longest first)
        sorted_roots = sorted(
            [normalize_path(r) for r in group_roots],
            key=len,
            reverse=True,
        )

        group_id = f"group:{group_name}"
        nodes.append(GraphNode(label="Group", properties={"id": group_id, "name": group_name}))

        for result in results:
            fpath = normalize_path(result.file_path)
            file_id = f"file:{_stable_id(fpath)}"

            repo_name = _match_repo(fpath, sorted_roots)
            repo_id = f"repo:{repo_name}"

            if repo_id not in seen_repo_ids:
                seen_repo_ids.add(repo_id)
                nodes.append(GraphNode(label="Repo", properties={"id": repo_id, "name": repo_name}))
                edges.append(
                    GraphEdge(
                        rel_type="CONTAINS",
                        from_label="Group",
                        from_id=group_id,
                        to_label="Repo",
                        to_id=repo_id,
                    )
                )

            if file_id not in seen_file_ids:
                seen_file_ids.add(file_id)
                nodes.append(GraphNode(label="File", properties={"id": file_id, "path": fpath}))
                edges.append(
                    GraphEdge(
                        rel_type="CONTAINS",
                        from_label="Repo",
                        from_id=repo_id,
                        to_label="File",
                        to_id=file_id,
                    )
                )

            for uri_match in result.uris:
                ep_id = f"endpoint:{_stable_id(uri_match.uri)}"
                ep_edge_key = f"{file_id}->CONTAINS->{ep_id}"
                nodes.append(
                    GraphNode(
                        label="Endpoint",
                        properties={"id": ep_id, "uri": uri_match.uri},
                    )
                )
                if ep_edge_key not in seen_endpoint_edges:
                    seen_endpoint_edges.add(ep_edge_key)
                    edges.append(
                        GraphEdge(
                            rel_type="CONTAINS",
                            from_label="File",
                            from_id=file_id,
                            to_label="Endpoint",
                            to_id=ep_id,
                        )
                    )

            for func in result.functions:
                func_id = f"func:{_stable_id(f'{fpath}::{func.name}:{func.line}')}"
                func_edge_key = f"{file_id}->CONTAINS->{func_id}"
                nodes.append(
                    GraphNode(
                        label="Function",
                        properties={
                            "id": func_id,
                            "name": func.name,
                            "line": func.line,
                            "snippet": func.snippet,
                            "file_path": fpath,
                        },
                    )
                )
                if func_edge_key not in seen_function_edges:
                    seen_function_edges.add(func_edge_key)
                    edges.append(
                        GraphEdge(
                            rel_type="CONTAINS",
                            from_label="File",
                            from_id=file_id,
                            to_label="Function",
                            to_id=func_id,
                        )
                    )

            for call in result.calls:
                caller_id = f"func:{_stable_id(call.caller)}"
                callee_id = f"func:{_stable_id(call.callee)}"
                call_type_rel = (
                    "CALLS_INTERNAL" if call.call_type == "internal" else "CALLS_EXTERNAL"
                )
                edges.append(
                    GraphEdge(
                        rel_type=call_type_rel,
                        from_label="Function",
                        from_id=caller_id,
                        to_label="Function",
                        to_id=callee_id,
                        properties={"line": call.line},
                    )
                )

        return nodes, edges

    def _discover_files(self, roots: Sequence[Path]) -> list[Path]:
        """Walk file trees, optionally respecting .gitignore."""
        files: list[Path] = []
        for root in roots:
            root = root.resolve()
            gitignore_spec = self._load_gitignore(root) if not self._ignore_gitignore else None

            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix not in _SCAN_EXTENSIONS:
                    continue
                if gitignore_spec is not None:
                    rel = path.relative_to(root)
                    if gitignore_spec.match_file(str(rel)):
                        continue
                files.append(path)
        return files

    def _parse_file(self, path: Path) -> ParseResult | None:
        """Dispatch a file to the first compatible parser."""
        last_error: Exception | None = None
        for parser in self._parsers:
            if parser.can_parse(path):
                try:
                    return parser.parse(path)
                except Exception as exc:
                    logger.warning("Parser %s failed on %s: %s", type(parser).__name__, path, exc)
                    last_error = exc
                    continue
        if last_error is not None:
            logger.error("All parsers failed for %s", path, exc_info=last_error)
        return None

    @staticmethod
    def _load_gitignore(root: Path) -> pathspec.PathSpec | None:
        """Load .gitignore from the given root directory."""
        gi = root / ".gitignore"
        if gi.is_file():
            lines = gi.read_text(encoding="utf-8").splitlines()
            return pathspec.PathSpec.from_lines("gitwildmatch", lines)
        return None


def _match_repo(fpath: str, sorted_roots: list[str]) -> str:
    """Determine repo name by longest-prefix matching against group root paths.

    Args:
        fpath: Normalized POSIX file path.
        sorted_roots: Normalized POSIX root paths, sorted longest-first.

    Returns:
        Repo name derived from the matching root's last path segment.
    """
    for root in sorted_roots:
        if fpath.startswith(root):
            segment = root.rstrip("/").rsplit("/", 1)[-1]
            return segment
    return _infer_repo_name_fallback(fpath)


def _infer_repo_name_fallback(normalized_posix_path: str) -> str:
    """Fallback heuristic when no root path matches a file."""
    parts = [p for p in normalized_posix_path.split("/") if p]
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].endswith(".git"):
            return parts[i].removesuffix(".git")
        if parts[i] in ("src", "lib", "pkg", "app"):
            if i > 0:
                return parts[i - 1]
    return parts[-2] if len(parts) >= 2 else "unknown"


def _stable_id(text: str) -> str:
    """Generate a deterministic short id from text.

    For pure file paths, normalize to POSIX first for cross-platform consistency.
    For composite keys (e.g. "path::name:line"), the path component is already
    normalized by the parser, so hash directly to avoid resolve() side effects.
    """
    if "::" in text:
        # Composite key like "path::func_name:line" — path already normalized
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    # Pure path — normalize for cross-platform consistency
    normalized = sanitize_for_id(text)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
