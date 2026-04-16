"""Trace engine: cross-repository call chain tracing via Neo4j Cypher queries."""

from __future__ import annotations

import logging
import re
from typing import Any

from bridgetrace.storage.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

_PARAM_RE = re.compile(r"\$\{[^}]*\}|\{[^}]*\}")

_TRACE_CYPHER = """
MATCH (ep:Endpoint {uri: $uri})<-[:CONTAINS]-(file:File)-[:CONTAINS]->(callee:Function)
MATCH path = (caller:Function)-[:CALLS_INTERNAL|CALLS_EXTERNAL*1..10]->(callee)
RETURN path
"""

_TRACE_FULL_CYPHER = """
MATCH (ep:Endpoint {uri: $uri})<-[:CONTAINS]-(file:File)-[:CONTAINS]->(callee:Function)
MATCH (caller:Function)-[:CALLS_INTERNAL|CALLS_EXTERNAL*1..10]->(callee)
MATCH (caller_file:File)-[:CONTAINS]->(caller)
MATCH (caller_repo:Repo)-[:CONTAINS]->(caller_file)
MATCH (caller_group:Group)-[:CONTAINS]->(caller_repo)
WHERE caller_group.name = $group
RETURN DISTINCT caller.name AS caller_name,
       caller.file_path AS caller_file,
       caller.line AS caller_line,
       callee.name AS callee_name,
       callee.file_path AS callee_file,
       ep.uri AS endpoint_uri,
       caller_group.name AS group_name
"""

_TRACE_URI_TO_IMPL_CYPHER = """
MATCH (ep:Endpoint {uri: $uri})-[:IMPLEMENTED_BY]->(impl:Function),
      (ep)<-[:CONTAINS*1..3]-(group:Group {name: $group})
RETURN ep.uri AS uri,
       impl.name AS impl_name,
       impl.file_path AS impl_file,
       impl.line AS impl_line
"""

_TRACE_ENDPOINT_CALLS_CYPHER = """
MATCH (src:Endpoint {uri: $uri})-[:CALLS]->(dst:Endpoint)
OPTIONAL MATCH (dst)-[:IMPLEMENTED_BY]->(func:Function)
RETURN dst.uri AS called_endpoint,
       dst.role AS called_role,
       dst.file_path AS called_file,
       dst.function_name AS called_function,
       func.name AS implementing_function,
       func.file_path AS function_file
"""

_TRACE_CROSS_REPO_CYPHER = """
MATCH (src:Endpoint {uri: $uri})-[r:ROUTES_TO|CALLS]->(dst:Endpoint)
OPTIONAL MATCH (dst)-[:IMPLEMENTED_BY]->(func:Function)
OPTIONAL MATCH (src)-[:IMPLEMENTED_BY]->(src_func:Function)
RETURN src.uri AS source_endpoint,
       src.role AS source_role,
       src.file_path AS source_file,
       src.function_name AS source_function,
       type(r) AS relation_type,
       dst.uri AS target_endpoint,
       dst.role AS target_role,
       dst.file_path AS target_file,
       dst.function_name AS target_function,
       func.name AS implementing_function,
       func.file_path AS function_file
"""

_TRACE_CONSUMES_CYPHER = """
MATCH (func:Function)-[:CONSUMES]->(ep:Endpoint {uri: $uri})
RETURN func.name AS consumer_function,
       func.file_path AS consumer_file,
       ep.uri AS consumed_endpoint
"""

_TRACE_CROSS_REPO_FULL_CYPHER = """
MATCH (ep:Endpoint {uri: $uri})
OPTIONAL MATCH (ep)-[:IMPLEMENTED_BY]->(impl_func:Function)
OPTIONAL MATCH (ep)-[:ROUTES_TO]->(routed_ep:Endpoint)
OPTIONAL MATCH (routed_ep)-[:IMPLEMENTED_BY]->(routed_func:Function)
OPTIONAL MATCH (ep)-[:CALLS]->(called_ep:Endpoint)
OPTIONAL MATCH (called_ep)-[:IMPLEMENTED_BY]->(called_func:Function)
RETURN ep.uri AS endpoint,
       ep.role AS role,
       ep.file_path AS file_path,
       ep.function_name AS function_name,
       impl_func.name AS implementing_function,
       impl_func.file_path AS implementing_file,
       routed_ep.uri AS routed_endpoint,
       routed_func.name AS routed_function,
       routed_func.file_path AS routed_file,
       called_ep.uri AS called_endpoint,
       called_func.name AS called_function,
       called_func.file_path AS called_file
"""

_TRACE_CROSS_REPO_MULTI_HOP_CYPHER = """
MATCH (ep:Endpoint {uri: $uri})
OPTIONAL MATCH (ep)-[:IMPLEMENTED_BY]->(impl_func:Function)
OPTIONAL MATCH path=(ep)-[:ROUTES_TO|CALLS*1..5]->(cross_ep:Endpoint)
OPTIONAL MATCH (cross_ep)-[:IMPLEMENTED_BY]->(cross_func:Function)
WITH ep, impl_func, path, cross_ep, cross_func,
     [n IN nodes(path) WHERE 'Endpoint' IN labels(n) | n.uri] AS chain_uris,
     [n IN nodes(path) WHERE 'Endpoint' IN labels(n) | n.role] AS chain_roles,
     [n IN nodes(path) WHERE 'Endpoint' IN labels(n) | n.file_path] AS chain_files,
     [r IN relationships(path) | type(r)] AS chain_rel_types
RETURN ep.uri AS endpoint,
       ep.role AS role,
       ep.file_path AS file_path,
       ep.function_name AS function_name,
       impl_func.name AS implementing_function,
       impl_func.file_path AS implementing_file,
       chain_uris,
       chain_roles,
       chain_files,
       chain_rel_types,
       cross_ep.uri AS cross_endpoint,
       cross_ep.role AS cross_role,
       cross_ep.file_path AS cross_file,
       cross_func.name AS cross_function,
       cross_func.file_path AS cross_file,
       length(path) AS hop_distance
ORDER BY hop_distance
"""

_TRACE_SUBPATH_FUZZY_CYPHER = """
UNWIND $ep_ids AS eid
MATCH (ep:Endpoint {id: eid})
OPTIONAL MATCH (ep)-[:ROUTES_TO]->(routed_ep:Endpoint)
OPTIONAL MATCH (ep)<-[:ROUTES_TO]-(upstream_ep:Endpoint)
OPTIONAL MATCH (ep)-[:IMPLEMENTED_BY]->(impl_func:Function)
OPTIONAL MATCH (routed_ep)-[:IMPLEMENTED_BY]->(routed_func:Function)
OPTIONAL MATCH (upstream_ep)-[:IMPLEMENTED_BY]->(upstream_func:Function)
RETURN ep.uri AS endpoint,
       ep.role AS role,
       ep.file_path AS file_path,
       ep.function_name AS function_name,
       ep.http_method AS http_method,
       impl_func.name AS implementing_function,
       impl_func.file_path AS implementing_file,
       routed_ep.uri AS routed_endpoint,
       routed_ep.role AS routed_role,
       routed_ep.http_method AS routed_http_method,
       routed_func.name AS routed_function,
       routed_func.file_path AS routed_file,
       upstream_ep.uri AS upstream_endpoint,
       upstream_ep.role AS upstream_role,
       upstream_ep.http_method AS upstream_http_method,
       upstream_func.name AS upstream_function,
       upstream_func.file_path AS upstream_file
"""

_TRACE_URI_CONTAINS_CYPHER = """
MATCH (ep:Endpoint)
WHERE ep.uri CONTAINS $suffix
OPTIONAL MATCH (ep)-[:ROUTES_TO]-(related:Endpoint)
OPTIONAL MATCH (ep)-[:IMPLEMENTED_BY]->(func:Function)
RETURN ep.uri AS endpoint,
       ep.role AS role,
       ep.file_path AS file_path,
       ep.http_method AS http_method,
       func.name AS implementing_function,
       func.file_path AS implementing_file,
       related.uri AS related_endpoint
ORDER BY size(ep.uri)
LIMIT 20
"""


class TraceResult:
    """Structured result of a trace operation."""

    def __init__(self, records: list[dict[str, Any]], strategy: str = "") -> None:
        self.records = records
        self.strategy = strategy

    def to_dict_list(self) -> list[dict[str, Any]]:
        """Return raw records as a list of dicts."""
        return self.records

    def format_text(self) -> str:
        """Format trace results as human-readable text."""
        if not self.records:
            return "No trace results found."
        lines: list[str] = []
        if self.strategy:
            lines.append(f"[Strategy: {self.strategy}]")
        for i, rec in enumerate(self.records, 1):
            hop_type = (
                "intra-service" if "caller_name" in rec or "callee_name" in rec else "cross-service"
            )
            lines.append(f"--- Hop {i} ({hop_type}) ---")
            for key, val in rec.items():
                if val is not None:
                    if isinstance(val, list):
                        val = " -> ".join(str(v) for v in val if v)
                    lines.append(f"  {key}: {val}")
        return "\n".join(lines)


def _normalize_uri_params(uri: str) -> str:
    """Normalize URI parameters: ${id} → {id}."""
    return _PARAM_RE.sub(lambda m: "{" + m.group(0).strip("${}").strip("{}") + "}", uri)


def _extract_subpath_keys(uri: str) -> list[str]:
    """Extract all sub-path keys of length >= 2 segments from a URI."""
    normalized = _PARAM_RE.sub("{}", uri)
    parts = [p for p in normalized.split("/") if p]
    keys: list[str] = []
    for i in range(len(parts) - 1):
        keys.append("/".join(parts[i:]))
    return keys


class TraceEngine:
    """Execute cross-repository call-chain traces against the Neo4j graph."""

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    def _find_endpoint_ids_by_uri(self, uri: str) -> list[str]:
        """Find Endpoint node IDs matching a URI (exact or normalized)."""
        records = self._client.run(
            "MATCH (ep:Endpoint {uri: $uri}) RETURN ep.id AS id",
            {"uri": uri},
        )
        if records:
            return [r["id"] for r in records if r.get("id")]

        normalized = _normalize_uri_params(uri)
        if normalized != uri:
            records = self._client.run(
                "MATCH (ep:Endpoint {uri: $uri}) RETURN ep.id AS id",
                {"uri": normalized},
            )
            if records:
                return [r["id"] for r in records if r.get("id")]

        return []

    def _find_endpoint_ids_by_subpath(self, uri: str, http_method: str = "") -> list[str]:
        """Find Endpoint node IDs sharing sub-paths with the given URI."""
        subpath_keys = _extract_subpath_keys(uri)
        if not subpath_keys:
            return []

        results: list[str] = []
        for sp_key in subpath_keys:
            records = self._client.run(
                """
                MATCH (ep:Endpoint)
                WHERE ep.uri CONTAINS $fragment
                RETURN DISTINCT ep.id AS id, ep.http_method AS http_method
                """,
                {"fragment": sp_key},
            )
            for r in records:
                if not r.get("id"):
                    continue
                ep_method = r.get("http_method", "")
                if http_method and ep_method and http_method != ep_method:
                    continue
                if r["id"] not in results:
                    results.append(r["id"])

        return results

    def trace_uri(self, uri: str, group: str | None = None, http_method: str = "") -> TraceResult:
        """Trace the full topology for a given URI path.

        Multi-strategy aggregation approach:
          Phase A: Intra-service call chain (exact + normalized)
          Phase B: Cross-service routing (multi-hop ROUTES_TO/CALLS)
          Phase C: Sub-path fuzzy match fallback
          Phase D: URI contains fallback

        Results from all successful phases are merged and deduplicated.
        """
        all_records: list[dict[str, Any]] = []
        strategy_parts: list[str] = []
        seen_sigs: set[str] = set()

        # Phase A: Intra-service call chain
        exact_uri = uri
        if group:
            records = self._client.run(_TRACE_FULL_CYPHER, {"uri": uri, "group": group})
        else:
            records = self._client.run(_TRACE_CYPHER, {"uri": uri})

        if not records:
            normalized = _normalize_uri_params(uri)
            if normalized != uri:
                logger.info("Exact match failed, trying normalized URI: %s -> %s", uri, normalized)
                exact_uri = normalized
                if group:
                    records = self._client.run(
                        _TRACE_FULL_CYPHER, {"uri": normalized, "group": group}
                    )
                else:
                    records = self._client.run(_TRACE_CYPHER, {"uri": normalized})
                if records:
                    strategy_parts.append("normalized_match")

        if records:
            if not strategy_parts:
                strategy_parts.append("exact_match")
            for r in records:
                sig = self._record_sig(r)
                if sig not in seen_sigs:
                    seen_sigs.add(sig)
                    all_records.append(r)

        # Phase B: Cross-service routing (always attempted)
        if records:
            uris_for_cross = [exact_uri]
        elif exact_uri != uri:
            uris_for_cross = [exact_uri, uri]
        else:
            uris_for_cross = [uri]

        cross_records: list[dict[str, Any]] = []
        for try_uri in uris_for_cross:
            cross_records = self._client.run(_TRACE_CROSS_REPO_MULTI_HOP_CYPHER, {"uri": try_uri})
            if cross_records:
                break
        if not cross_records:
            for try_uri in uris_for_cross:
                cross_records = self._client.run(_TRACE_CROSS_REPO_FULL_CYPHER, {"uri": try_uri})
                if cross_records:
                    break

        if cross_records:
            for r in cross_records:
                sig = self._record_sig(r)
                if sig not in seen_sigs:
                    seen_sigs.add(sig)
                    all_records.append(r)
            strategy_parts.append("cross_repo")

        if all_records:
            return TraceResult(all_records, "+".join(strategy_parts))

        # Phase C: Sub-path fuzzy match
        logger.info("Exact + cross-repo failed, trying sub-path fuzzy: %s", uri)
        ep_ids = self._find_endpoint_ids_by_subpath(uri, http_method)
        if ep_ids:
            records = self._client.run(_TRACE_SUBPATH_FUZZY_CYPHER, {"ep_ids": ep_ids})
            if records:
                return TraceResult(records, "subpath_fuzzy")

        # Phase D: URI contains fallback
        logger.info("Sub-path fuzzy failed, trying URI contains: %s", uri)
        suffix = _PARAM_RE.sub("{}", uri).rsplit("/", 2)[-1] if "/" in uri else uri
        if suffix:
            records = self._client.run(_TRACE_URI_CONTAINS_CYPHER, {"suffix": suffix})
            if records:
                return TraceResult(records, "uri_contains")

        return TraceResult([], "")

    @staticmethod
    def _record_sig(rec: dict[str, Any]) -> str:
        """Lightweight dedup signature for a trace record."""
        return "|".join(f"{k}={v}" for k, v in sorted(rec.items()) if v is not None)

    def trace_uri_to_implementation(self, uri: str, group: str) -> TraceResult:
        """Trace from URI to backend implementation function."""
        records = self._client.run(
            _TRACE_URI_TO_IMPL_CYPHER,
            {"uri": uri, "group": group},
        )
        if records:
            return TraceResult(records, "exact_match")

        normalized = _normalize_uri_params(uri)
        if normalized != uri:
            records = self._client.run(
                _TRACE_URI_TO_IMPL_CYPHER,
                {"uri": normalized, "group": group},
            )
            if records:
                return TraceResult(records, "normalized_match")

        return TraceResult([], "")

    def trace_endpoint_calls(self, uri: str) -> TraceResult:
        """Trace which other endpoints are called by the given endpoint."""
        records = self._client.run(
            _TRACE_ENDPOINT_CALLS_CYPHER,
            {"uri": uri},
        )
        if records:
            return TraceResult(records, "exact_match")

        normalized = _normalize_uri_params(uri)
        if normalized != uri:
            records = self._client.run(
                _TRACE_ENDPOINT_CALLS_CYPHER,
                {"uri": normalized},
            )
            if records:
                return TraceResult(records, "normalized_match")

        return TraceResult([], "")

    def trace_cross_repo(self, uri: str) -> TraceResult:
        """Trace cross-repository routing: gateway endpoint -> backend endpoint."""
        records = self._client.run(
            _TRACE_CROSS_REPO_CYPHER,
            {"uri": uri},
        )
        if records:
            return TraceResult(records, "exact_match")

        normalized = _normalize_uri_params(uri)
        if normalized != uri:
            records = self._client.run(
                _TRACE_CROSS_REPO_CYPHER,
                {"uri": normalized},
            )
            if records:
                return TraceResult(records, "normalized_match")

        return TraceResult([], "")

    def trace_cross_repo_full(self, uri: str) -> TraceResult:
        """Comprehensive cross-repo trace: implementation + ROUTES_TO + CALLS."""
        records = self._client.run(
            _TRACE_CROSS_REPO_FULL_CYPHER,
            {"uri": uri},
        )
        if records:
            return TraceResult(records, "exact_match")

        normalized = _normalize_uri_params(uri)
        if normalized != uri:
            records = self._client.run(
                _TRACE_CROSS_REPO_FULL_CYPHER,
                {"uri": normalized},
            )
            if records:
                return TraceResult(records, "normalized_match")

        return TraceResult([], "")

    def trace_consumers(self, uri: str) -> TraceResult:
        """Find which functions consume the given endpoint via HTTP calls."""
        records = self._client.run(
            _TRACE_CONSUMES_CYPHER,
            {"uri": uri},
        )
        if records:
            return TraceResult(records, "exact_match")

        normalized = _normalize_uri_params(uri)
        if normalized != uri:
            records = self._client.run(
                _TRACE_CONSUMES_CYPHER,
                {"uri": normalized},
            )
            if records:
                return TraceResult(records, "normalized_match")

        return TraceResult([], "")
