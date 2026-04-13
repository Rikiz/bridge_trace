"""Domain models for the knowledge graph and parse results."""

from __future__ import annotations

from pydantic import BaseModel, Field


class URIMatch(BaseModel):
    """A URI path string extracted from a source or config file."""

    uri: str
    source_file: str


class FunctionDef(BaseModel):
    """A function / method definition extracted by tree-sitter."""

    name: str
    file_path: str
    line: int
    snippet: str = ""


class CallEdge(BaseModel):
    """A call relationship between two functions."""

    caller: str
    callee: str
    call_type: str = Field(description="internal | external")
    line: int = 0


class EndpointImpl(BaseModel):
    """Maps an endpoint URI to the function that implements it."""

    uri: str
    function_name: str
    function_line: int


class ParseResult(BaseModel):
    """Aggregated result from a single file parse."""

    file_path: str
    uris: list[URIMatch] = Field(default_factory=list)
    functions: list[FunctionDef] = Field(default_factory=list)
    calls: list[CallEdge] = Field(default_factory=list)
    endpoint_impls: list[EndpointImpl] = Field(default_factory=list)


class GraphNode(BaseModel):
    """Generic graph node for Neo4j ingestion."""

    label: str
    properties: dict[str, object] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """Generic graph edge for Neo4j ingestion."""

    rel_type: str
    from_label: str
    from_key: str = "id"
    to_label: str
    to_key: str = "id"
    from_id: str
    to_id: str
    properties: dict[str, object] = Field(default_factory=dict)
