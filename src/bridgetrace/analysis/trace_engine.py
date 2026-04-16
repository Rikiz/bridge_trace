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
            lines.append(f"--- Hop {i} ---")
            for key, val in rec.items():
                if val is not None:
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

        Multi-strategy approach:
          1. Exact URI match (call-chain + cross-repo)
          2. Normalized URI match (${id} → {id})
          3. Sub-path fuzzy match
          4. URI contains fallback
        """
        # Strategy 1: Exact match
        if group:
            records = self._client.run(_TRACE_FULL_CYPHER, {"uri": uri, "group": group})
        else:
            records = self._client.run(_TRACE_CYPHER, {"uri": uri})

        if records:
            return TraceResult(records, "exact_match")

        # Try cross-repo via exact URI
        records = self._client.run(_TRACE_CROSS_REPO_FULL_CYPHER, {"uri": uri})
        if records:
            return TraceResult(records, "exact_cross_repo")

        # Strategy 2: Normalized URI match
        normalized = _normalize_uri_params(uri)
        if normalized != uri:
            logger.info("Exact match failed, trying normalized URI: %s → %s", uri, normalized)
            if group:
                records = self._client.run(_TRACE_FULL_CYPHER, {"uri": normalized, "group": group})
            else:
                records = self._client.run(_TRACE_CYPHER, {"uri": normalized})
            if not records:
                records = self._client.run(_TRACE_CROSS_REPO_FULL_CYPHER, {"uri": normalized})
            if records:
                return TraceResult(records, "normalized_match")

        # Strategy 3: Sub-path fuzzy match
        logger.info("Normalized match failed, trying sub-path fuzzy: %s", uri)
        ep_ids = self._find_endpoint_ids_by_subpath(uri, http_method)
        if ep_ids:
            records = self._client.run(_TRACE_SUBPATH_FUZZY_CYPHER, {"ep_ids": ep_ids})
            if records:
                return TraceResult(records, "subpath_fuzzy")

        # Strategy 4: URI contains fallback
        logger.info("Sub-path fuzzy failed, trying URI contains: %s", uri)
        suffix = _PARAM_RE.sub("{}", uri).rsplit("/", 2)[-1] if "/" in uri else uri
        if suffix:
            records = self._client.run(_TRACE_URI_CONTAINS_CYPHER, {"suffix": suffix})
            if records:
                return TraceResult(records, "uri_contains")

        return TraceResult([], "")

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
