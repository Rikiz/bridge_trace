"""Core scanner: walks file trees, dispatches to parsers, builds graph data."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Sequence
from pathlib import Path

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

_PARAM_RE = re.compile(r"\$\{[^}]*\}|\{[^}]*\}")


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

        Multi-phase approach:
          Phase 1: Create all nodes + CONTAINS edges, build indexes
          Phase 2: CALLS_INTERNAL / CALLS_EXTERNAL edges
          Phase 2.5: CONSUMES edges (Function → Endpoint via HTTP calls)
          Phase 3: IMPLEMENTS / IMPLEMENTED_BY / DEFINED_IN edges
          Phase 3.5: ROUTES_TO edges (declaration Endpoint → implementation Endpoint)
          Phase 4: Endpoint CALLS Endpoint (derived from all paths)
        """
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        sorted_roots = sorted(
            [normalize_path(r) for r in group_roots],
            key=len,
            reverse=True,
        )

        # ── Phase 1: Nodes + CONTAINS ────────────────────────────────
        seen_repo_ids: set[str] = set()
        seen_file_ids: set[str] = set()
        seen_endpoint_edges: set[str] = set()
        seen_function_edges: set[str] = set()

        func_key_to_id: dict[str, str] = {}
        func_name_to_ids: dict[str, list[tuple[str, str]]] = {}
        func_id_to_ep_ids: dict[str, set[str]] = {}
        ep_id_to_func_ids: dict[str, set[str]] = {}
        ep_id_to_file_id: dict[str, str] = {}
        uri_to_ep_id: dict[str, str] = {}
        ep_id_to_role: dict[str, str] = {}
        ep_id_to_func_name: dict[str, str] = {}

        # Build endpoint_impls index: (uri, file_path) -> function_name
        impl_uri_to_func: dict[tuple[str, str], str] = {}
        for result in results:
            fpath = normalize_path(result.file_path)
            for impl in result.endpoint_impls:
                impl_uri_to_func[(impl.uri, fpath)] = impl.function_name

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

                func_name = ""
                role = uri_match.role or "reference"
                if role == "implementation":
                    func_name = impl_uri_to_func.get((uri_match.uri, fpath), "")

                nodes.append(
                    GraphNode(
                        label="Endpoint",
                        properties={
                            "id": ep_id,
                            "uri": uri_match.uri,
                            "role": role,
                            "file_path": fpath,
                            "function_name": func_name,
                        },
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
                ep_id_to_file_id[ep_id] = file_id
                uri_to_ep_id[uri_match.uri] = ep_id
                ep_id_to_role[ep_id] = role
                if func_name:
                    ep_id_to_func_name[ep_id] = func_name

            for func in result.functions:
                func_key = f"{fpath}::{func.name}:{func.line}"
                func_id = f"func:{_stable_id(func_key)}"
                func_key_to_id[func_key] = func_id
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
                func_name_to_ids.setdefault(func.name, []).append((repo_name, func_id))

            for impl in result.endpoint_impls:
                func_key = f"{fpath}::{impl.function_name}:{impl.function_line}"
                func_id = func_key_to_id.get(func_key, f"func:{_stable_id(func_key)}")
                ep_id = f"endpoint:{_stable_id(impl.uri)}"
                func_id_to_ep_ids.setdefault(func_id, set()).add(ep_id)
                ep_id_to_func_ids.setdefault(ep_id, set()).add(func_id)
                ep_id_to_role[ep_id] = "implementation"
                ep_id_to_func_name[ep_id] = impl.function_name

        # ── Phase 2: Call edges ───────────────────────────────────────
        for result in results:
            fpath = normalize_path(result.file_path)
            repo_name = _match_repo(fpath, sorted_roots)

            for call in result.calls:
                if call.call_type == "internal":
                    caller_id = f"func:{_stable_id(call.caller)}"
                    callee_id = f"func:{_stable_id(call.callee)}"
                    edges.append(
                        GraphEdge(
                            rel_type="CALLS_INTERNAL",
                            from_label="Function",
                            from_id=caller_id,
                            to_label="Function",
                            to_id=callee_id,
                            properties={"line": call.line},
                        )
                    )
                else:
                    caller_id = f"func:{_stable_id(call.caller)}"
                    callee_id = _resolve_external_callee(call.callee, repo_name, func_name_to_ids)
                    if callee_id is not None:
                        edges.append(
                            GraphEdge(
                                rel_type="CALLS_EXTERNAL",
                                from_label="Function",
                                from_id=caller_id,
                                to_label="Function",
                                to_id=callee_id,
                                properties={"line": call.line},
                            )
                        )

        # ── Phase 2.5: CONSUMES edges (Function → Endpoint via HTTP) ──
        # Also enrich Endpoint function_name for reference-role endpoints
        for result in results:
            fpath = normalize_path(result.file_path)

            for hc in result.http_calls:
                caller_func_id = f"func:{_stable_id(hc.caller)}"

                target_ep_id = uri_to_ep_id.get(hc.uri)
                if target_ep_id is None:
                    target_ep_id = _fuzzy_match_endpoint(hc.uri, uri_to_ep_id, ep_id_to_role)

                if target_ep_id is not None:
                    edges.append(
                        GraphEdge(
                            rel_type="CONSUMES",
                            from_label="Function",
                            from_id=caller_func_id,
                            to_label="Endpoint",
                            to_id=target_ep_id,
                            properties={"http_method": hc.http_method, "line": hc.line},
                        )
                    )

                    if target_ep_id not in ep_id_to_func_name:
                        caller_func_key = hc.caller
                        caller_short = caller_func_key.rsplit("::", 1)[-1].rsplit(":", 1)[0]
                        ep_id_to_func_name[target_ep_id] = caller_short
                    if target_ep_id not in ep_id_to_role:
                        ep_id_to_role[target_ep_id] = "reference"

        # ── Phase 3: IMPLEMENTS / IMPLEMENTED_BY / DEFINED_IN ─────────
        seen_impl_edges: set[str] = set()

        for func_id, ep_ids in func_id_to_ep_ids.items():
            for ep_id in ep_ids:
                edge_key = f"{func_id}->IMPLEMENTS->{ep_id}"
                if edge_key not in seen_impl_edges:
                    seen_impl_edges.add(edge_key)
                    edges.append(
                        GraphEdge(
                            rel_type="IMPLEMENTS",
                            from_label="Function",
                            from_id=func_id,
                            to_label="Endpoint",
                            to_id=ep_id,
                        )
                    )

        for ep_id, func_ids in ep_id_to_func_ids.items():
            for func_id in func_ids:
                edge_key = f"{ep_id}->IMPLEMENTED_BY->{func_id}"
                if edge_key not in seen_impl_edges:
                    seen_impl_edges.add(edge_key)
                    edges.append(
                        GraphEdge(
                            rel_type="IMPLEMENTED_BY",
                            from_label="Endpoint",
                            from_id=ep_id,
                            to_label="Function",
                            to_id=func_id,
                        )
                    )

        for ep_id, file_id in ep_id_to_file_id.items():
            edge_key = f"{ep_id}->DEFINED_IN->{file_id}"
            if edge_key not in seen_impl_edges:
                seen_impl_edges.add(edge_key)
                edges.append(
                    GraphEdge(
                        rel_type="DEFINED_IN",
                        from_label="Endpoint",
                        from_id=ep_id,
                        to_label="File",
                        to_id=file_id,
                    )
                )

        # ── Phase 3.5: ROUTES_TO (declaration → implementation) ──────
        declaration_eps = [
            (ep_id, ep_props["uri"])
            for ep_id, ep_props in _iter_endpoint_nodes(nodes)
            if ep_props.get("role") == "declaration"
        ]
        implementation_eps = [
            (ep_id, ep_props["uri"])
            for ep_id, ep_props in _iter_endpoint_nodes(nodes)
            if ep_props.get("role") == "implementation"
        ]

        impl_suffix_index: dict[str, list[tuple[str, str]]] = {}
        for impl_ep_id, impl_uri in implementation_eps:
            norm_impl = _PARAM_RE.sub("{}", impl_uri)
            impl_parts = tuple(p for p in norm_impl.split("/") if p)
            if len(impl_parts) >= 2:
                key = "/".join(impl_parts)
                impl_suffix_index.setdefault(key, []).append((impl_ep_id, impl_uri))

        seen_routes_edges: set[str] = set()
        for decl_ep_id, decl_uri in declaration_eps:
            norm_decl = _PARAM_RE.sub("{}", decl_uri)
            decl_parts = [p for p in norm_decl.split("/") if p]
            matched = False
            for suffix_len in range(min(len(decl_parts), 8), 1, -1):
                suffix_key = "/".join(decl_parts[-suffix_len:])
                if suffix_key in impl_suffix_index:
                    for impl_ep_id, impl_uri in impl_suffix_index[suffix_key]:
                        if _uri_suffix_match(decl_uri, impl_uri):
                            edge_key = f"{decl_ep_id}->ROUTES_TO->{impl_ep_id}"
                            if edge_key not in seen_routes_edges:
                                seen_routes_edges.add(edge_key)
                                edges.append(
                                    GraphEdge(
                                        rel_type="ROUTES_TO",
                                        from_label="Endpoint",
                                        from_id=decl_ep_id,
                                        to_label="Endpoint",
                                        to_id=impl_ep_id,
                                    )
                                )
                    matched = True
                    break
            if not matched:
                for impl_ep_id, impl_uri in implementation_eps:
                    if _uri_suffix_match(decl_uri, impl_uri):
                        edge_key = f"{decl_ep_id}->ROUTES_TO->{impl_ep_id}"
                        if edge_key not in seen_routes_edges:
                            seen_routes_edges.add(edge_key)
                            edges.append(
                                GraphEdge(
                                    rel_type="ROUTES_TO",
                                    from_label="Endpoint",
                                    from_id=decl_ep_id,
                                    to_label="Endpoint",
                                    to_id=impl_ep_id,
                                )
                            )

        # ── Phase 4: Endpoint CALLS Endpoint (derived) ───────────────
        seen_ep_call_edges: set[str] = set()

        # Path 1: func implements epA, func CONSUMES epB → epA CALLS epB
        for edge in edges:
            if edge.rel_type != "CONSUMES":
                continue
            caller_func_id = edge.from_id
            target_ep_id = edge.to_id
            caller_eps = func_id_to_ep_ids.get(caller_func_id, set())
            for src_ep in caller_eps:
                if src_ep == target_ep_id:
                    continue
                ep_call_key = f"{src_ep}->CALLS->{target_ep_id}"
                if ep_call_key not in seen_ep_call_edges:
                    seen_ep_call_edges.add(ep_call_key)
                    edges.append(
                        GraphEdge(
                            rel_type="CALLS",
                            from_label="Endpoint",
                            from_id=src_ep,
                            to_label="Endpoint",
                            to_id=target_ep_id,
                        )
                    )

        # Path 2: ROUTES_TO → CALLS (gateway ep → backend ep)
        for edge in list(edges):
            if edge.rel_type != "ROUTES_TO":
                continue
            src_ep = edge.from_id
            dst_ep = edge.to_id
            ep_call_key = f"{src_ep}->CALLS->{dst_ep}"
            if ep_call_key not in seen_ep_call_edges:
                seen_ep_call_edges.add(ep_call_key)
                edges.append(
                    GraphEdge(
                        rel_type="CALLS",
                        from_label="Endpoint",
                        from_id=src_ep,
                        to_label="Endpoint",
                        to_id=dst_ep,
                    )
                )

        # Path 3: func implements epA, func calls funcB, funcB implements epB
        for edge in edges:
            if edge.rel_type not in ("CALLS_INTERNAL", "CALLS_EXTERNAL"):
                continue
            caller_func_id = edge.from_id
            callee_func_id = edge.to_id
            caller_eps = func_id_to_ep_ids.get(caller_func_id, set())
            callee_eps = func_id_to_ep_ids.get(callee_func_id, set())
            for src_ep in caller_eps:
                for dst_ep in callee_eps:
                    if src_ep == dst_ep:
                        continue
                    ep_call_key = f"{src_ep}->CALLS->{dst_ep}"
                    if ep_call_key not in seen_ep_call_edges:
                        seen_ep_call_edges.add(ep_call_key)
                        edges.append(
                            GraphEdge(
                                rel_type="CALLS",
                                from_label="Endpoint",
                                from_id=src_ep,
                                to_label="Endpoint",
                                to_id=dst_ep,
                            )
                        )

        # Enrich Endpoint nodes with final role/function_name
        for _i, node in enumerate(nodes):
            if node.label != "Endpoint":
                continue
            ep_id = node.properties["id"]
            if ep_id in ep_id_to_role and "role" not in node.properties:
                node.properties["role"] = ep_id_to_role[ep_id]
            if ep_id in ep_id_to_func_name and "function_name" not in node.properties:
                node.properties["function_name"] = ep_id_to_func_name[ep_id]

        return nodes, edges

    def _discover_files(self, roots: Sequence[Path]) -> list[Path]:
        """Walk file trees, optionally respecting nested .gitignore files."""
        files: list[Path] = []
        for root in roots:
            root = root.resolve()
            if self._ignore_gitignore:
                for path in root.rglob("*"):
                    if path.is_file() and path.suffix in _SCAN_EXTENSIONS:
                        files.append(path)
            else:
                files.extend(self._walk_with_gitignore(root))
        return files

    def _walk_with_gitignore(self, root: Path) -> list[Path]:
        """Walk a directory tree respecting nested .gitignore files."""
        files: list[Path] = []
        spec_stack: list[tuple[str, pathspec.PathSpec]] = []

        root_spec = self._load_gitignore(root)
        if root_spec is not None:
            spec_stack.append(("", root_spec))

        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)

            gi_path = os.path.join(dirpath, ".gitignore")
            if os.path.isfile(gi_path):
                lines = Path(gi_path).read_text(encoding="utf-8").splitlines()
                dir_spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
                spec_stack.append((rel_dir, dir_spec))

            for fname in filenames:
                fpath = Path(dirpath) / fname
                if fpath.suffix not in _SCAN_EXTENSIONS:
                    continue
                rel = os.path.relpath(str(fpath), str(root))
                if self._is_ignored(rel, spec_stack):
                    continue
                files.append(fpath)

            removed_dirs: list[str] = []
            for dname in dirnames:
                d_rel = os.path.relpath(os.path.join(dirpath, dname), str(root))
                if self._is_ignored(d_rel, spec_stack, is_dir=True):
                    removed_dirs.append(dname)
            for d in removed_dirs:
                dirnames.remove(d)

            if os.path.normpath(rel_dir) not in [os.path.normpath(s[0]) for s in spec_stack]:
                pass

        return files

    @staticmethod
    def _is_ignored(
        rel_path: str,
        spec_stack: list[tuple[str, pathspec.PathSpec]],
        is_dir: bool = False,
    ) -> bool:
        """Check if a relative path is ignored by any gitignore in the stack."""
        for base_dir, spec in spec_stack:
            if base_dir and not rel_path.startswith(base_dir.replace(os.sep, "/") + "/"):
                continue
            if spec.match_file(rel_path):
                return True
            if is_dir and spec.match_file(rel_path + "/"):
                return True
        return False

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


def _iter_endpoint_nodes(nodes: list[GraphNode]):
    """Yield (ep_id, properties) for Endpoint nodes."""
    for node in nodes:
        if node.label == "Endpoint":
            yield node.properties["id"], node.properties


def _uri_suffix_match(decl_uri: str, impl_uri: str) -> bool:
    """Check if a declaration URI suffix-matches an implementation URI.

    Example:
      /data/v1/tanet-config/{userid} vs /v1/tanet-config/{userid} → True
      /api/v1/users vs /v1/users → True
      /v1/users vs /v1/orders → False
    """
    norm_decl = _PARAM_RE.sub("{}", decl_uri)
    norm_impl = _PARAM_RE.sub("{}", impl_uri)
    decl_parts = [p for p in norm_decl.split("/") if p]
    impl_parts = [p for p in norm_impl.split("/") if p]
    if len(impl_parts) < 2:
        return False
    if len(decl_parts) < len(impl_parts):
        return False
    decl_tail = decl_parts[-len(impl_parts) :]
    return decl_tail == impl_parts


def _fuzzy_match_endpoint(
    uri: str,
    uri_to_ep_id: dict[str, str],
    ep_id_to_role: dict[str, str],
) -> str | None:
    """Try suffix-matching a URI against implementation-role endpoints."""
    for existing_uri, ep_id in uri_to_ep_id.items():
        if ep_id_to_role.get(ep_id) != "implementation":
            continue
        if _uri_suffix_match(uri, existing_uri) or _uri_suffix_match(existing_uri, uri):
            return ep_id
    return None


def _match_repo(fpath: str, sorted_roots: list[str]) -> str:
    """Determine repo name by longest-prefix matching against group root paths."""
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
        if parts[i] in ("src", "lib", "pkg", "app") and i > 0:
            return parts[i - 1]
    return parts[-2] if len(parts) >= 2 else "unknown"


def _resolve_external_callee(
    callee_name: str,
    caller_repo: str,
    func_name_to_ids: dict[str, list[tuple[str, str]]],
) -> str | None:
    """Resolve an external callee name to a func_id."""
    candidates = _lookup_callee(callee_name, caller_repo, func_name_to_ids)
    if candidates is None and "." in callee_name:
        short_name = callee_name.rsplit(".", 1)[-1]
        candidates = _lookup_callee(short_name, caller_repo, func_name_to_ids)
    return candidates


def _lookup_callee(
    name: str,
    caller_repo: str,
    func_name_to_ids: dict[str, list[tuple[str, str]]],
) -> str | None:
    """Look up a callee name, preferring same-repo matches."""
    entries = func_name_to_ids.get(name)
    if not entries:
        return None
    same_repo = [fid for repo, fid in entries if repo == caller_repo]
    if len(same_repo) == 1:
        return same_repo[0]
    if len(entries) == 1:
        return entries[0][1]
    return None


def _stable_id(text: str) -> str:
    """Generate a deterministic short id from text."""
    if "::" in text:
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    normalized = sanitize_for_id(text)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
