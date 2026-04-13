"""Trace engine: cross-repository call chain tracing via Neo4j Cypher queries."""

from __future__ import annotations

import logging
from typing import Any

from bridgetrace.storage.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

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
MATCH (ep:Endpoint {uri: $uri})<-[:CONTAINS*1..3]-(impl:Function),
      (impl)<-[:IMPLEMENTS]-(controller:Function),
      (group:Group)-[:CONTAINS*1..3]->(ep)
WHERE group.name = $group
RETURN ep.uri AS uri,
       impl.name AS impl_name,
       impl.file_path AS impl_file,
       impl.line AS impl_line,
       controller.name AS controller_name,
       controller.file_path AS controller_file
"""


class TraceResult:
    """Structured result of a trace operation."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def to_dict_list(self) -> list[dict[str, Any]]:
        """Return raw records as a list of dicts."""
        return self.records

    def format_text(self) -> str:
        """Format trace results as human-readable text."""
        if not self.records:
            return "No trace results found."
        lines: list[str] = []
        for i, rec in enumerate(self.records, 1):
            lines.append(f"--- Hop {i} ---")
            for key, val in rec.items():
                lines.append(f"  {key}: {val}")
        return "\n".join(lines)


class TraceEngine:
    """Execute cross-repository call-chain traces against the Neo4j graph."""

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    def trace_uri(self, uri: str, group: str | None = None) -> TraceResult:
        """Trace the full topology for a given URI path."""
        if group:
            records = self._client.run(
                _TRACE_FULL_CYPHER,
                {"uri": uri, "group": group},
            )
        else:
            records = self._client.run(_TRACE_CYPHER, {"uri": uri})

        if not records:
            logger.info("No trace found for URI: %s", uri)

        return TraceResult(records)

    def trace_uri_to_implementation(self, uri: str, group: str) -> TraceResult:
        """Trace from URI to backend implementation function."""
        records = self._client.run(
            _TRACE_URI_TO_IMPL_CYPHER,
            {"uri": uri, "group": group},
        )
        return TraceResult(records)
