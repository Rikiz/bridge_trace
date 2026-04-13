"""Neo4j client with schema management and batch ingestion."""

from __future__ import annotations

import logging
from typing import Any, Sequence

from neo4j import GraphDatabase, Driver

from bridgetrace.config import settings
from bridgetrace.models.graph import GraphNode, GraphEdge

logger = logging.getLogger(__name__)

SCHEMA_CONSTRAINTS: list[str] = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Group)      REQUIRE n.id   IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Repo)       REQUIRE n.id   IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:File)       REQUIRE n.id   IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Function)   REQUIRE n.id   IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Endpoint)   REQUIRE n.id   IS UNIQUE",
]

SCHEMA_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS FOR (n:Endpoint) ON (n.uri)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Function) ON (n.name)",
    "CREATE INDEX IF NOT EXISTS FOR (n:File) ON (n.path)",
    "CREATE INDEX IF NOT EXISTS FOR ()-[r:CALLS]-() ON (type(r))",
    "CREATE INDEX IF NOT EXISTS FOR ()-[r:CALLS_EXTERNAL]-() ON (type(r))",
    "CREATE INDEX IF NOT EXISTS FOR ()-[r:IMPLEMENTS]-() ON (type(r))",
    "CREATE INDEX IF NOT EXISTS FOR ()-[r:DEFINED_IN]-() ON (type(r))",
]


class Neo4jClient:
    """High-level Neo4j client with schema bootstrap and batch writes."""

    def __init__(
        self,
        uri: str | None = None,
        auth: tuple[str, str] | None = None,
        database: str | None = None,
    ) -> None:
        self._uri = uri or settings.neo4j_uri
        self._auth = auth or settings.neo4j_auth
        self._database = database or settings.neo4j_database
        self._driver: Driver | None = None

    @property
    def driver(self) -> Driver:
        """Lazily create the Neo4j driver."""
        if self._driver is None:
            self._driver = GraphDatabase.driver(self._uri, auth=self._auth)
        return self._driver

    def close(self) -> None:
        """Close the underlying driver connection."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> Neo4jClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def bootstrap_schema(self) -> None:
        """Create constraints and indexes for the knowledge graph."""
        with self.driver.session(database=self._database) as session:
            for stmt in SCHEMA_CONSTRAINTS + SCHEMA_INDEXES:
                session.run(stmt)
        logger.info("Neo4j schema bootstrapped successfully")

    def run(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute an arbitrary Cypher query and return records."""
        with self.driver.session(database=self._database) as session:
            result = session.run(cypher, params or {})
            return [record.data() for record in result]

    def batch_merge_nodes(self, nodes: Sequence[GraphNode], batch_size: int = 0) -> None:
        """Batch-merge nodes using UNWIND for efficient writes."""
        if not nodes:
            return
        bs = batch_size or settings.scan_batch_size

        by_label: dict[str, list[GraphNode]] = {}
        for n in nodes:
            by_label.setdefault(n.label, []).append(n)

        with self.driver.session(database=self._database) as session:
            for label, group in by_label.items():
                for i in range(0, len(group), bs):
                    chunk = group[i : i + bs]
                    props_list = [n.properties for n in chunk]
                    session.run(
                        f"""
                        UNWIND $rows AS row
                        MERGE (n:{label} {{id: row.id}})
                        SET n += row
                        """,
                        rows=props_list,
                    )
                logger.info("Merged %d %s nodes", len(group), label)

    def batch_merge_edges(self, edges: Sequence[GraphEdge], batch_size: int = 0) -> None:
        """Batch-merge edges using UNWIND."""
        if not edges:
            return
        bs = batch_size or settings.scan_batch_size

        # Group edges by (rel_type, from_label, to_label) for efficient matching
        by_key: dict[tuple[str, str, str], list[GraphEdge]] = {}
        for e in edges:
            key = (e.rel_type, e.from_label, e.to_label)
            by_key.setdefault(key, []).append(e)

        with self.driver.session(database=self._database) as session:
            for (rel, from_label, to_label), group in by_key.items():
                for i in range(0, len(group), bs):
                    chunk = group[i : i + bs]
                    rows = [
                        {
                            "from_id": e.from_id,
                            "to_id": e.to_id,
                            "props": e.properties,
                        }
                        for e in chunk
                    ]
                    session.run(
                        f"""
                        UNWIND $rows AS row
                        MATCH (a:{from_label} {{id: row.from_id}})
                        MATCH (b:{to_label} {{id: row.to_id}})
                        MERGE (a)-[r:{rel}]->(b)
                        SET r += row.props
                        """,
                        rows=rows,
                    )
                logger.info("Merged %d %s edges (%s->%s)", len(group), rel, from_label, to_label)
