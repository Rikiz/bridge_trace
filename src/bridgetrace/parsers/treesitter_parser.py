"""Tree-sitter based semantic parser for Python, TypeScript, and Java.

Extracts string literals, function definitions (with line numbers & snippets),
and internal call graphs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tree_sitter import Node

from bridgetrace.models.graph import ParseResult, FunctionDef, CallEdge, URIMatch
from bridgetrace.parsers.base import BaseParser
from bridgetrace.parsers.json_parser import URI_PATH_RE

_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
}


def _get_language_name(path: Path) -> str | None:
    """Return tree-sitter language name for a file extension."""
    return _LANG_MAP.get(path.suffix)


def _node_text(node: Node, source: bytes) -> str:
    """Extract the text of a tree-sitter node from source bytes."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_nodes_by_type(root: Node, type_name: str) -> list[Node]:
    """Walk the tree and collect all nodes of a given type."""
    results: list[Node] = []

    def _walk(node: Node) -> None:
        if node.type == type_name:
            results.append(node)
        for child in node.children:
            _walk(child)

    _walk(root)
    return results


class TreeSitterParser(BaseParser):
    """Semantic parser using tree-sitter for Python/TS/Java source files."""

    supported_extensions = tuple(_LANG_MAP.keys())

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}

    def _get_parser(self, lang: str) -> Any:
        """Lazily initialise a tree-sitter parser for the given language."""
        if lang not in self._parsers:
            from tree_sitter_languages import get_parser

            parser = get_parser(lang)
            self._parsers[lang] = parser
        return self._parsers[lang]

    def parse(self, path: Path) -> ParseResult:
        """Parse a source file and extract string literals, functions, and calls."""
        lang = _get_language_name(path)
        if lang is None:
            return ParseResult(file_path=str(path), uris=[], functions=[], calls=[])

        source = path.read_bytes()
        parser = self._get_parser(lang)
        tree = parser.parse(source)
        root = tree.rootNode

        uris = self._extract_uri_literals(root, source, str(path))
        functions = self._extract_functions(root, source, str(path), lang)
        calls = self._extract_calls(root, source, str(path), lang)

        return ParseResult(
            file_path=str(path),
            uris=uris,
            functions=functions,
            calls=calls,
        )

    def _extract_uri_literals(self, root: Node, source: bytes, file_path: str) -> list[URIMatch]:
        """Extract string literals that match the URI path pattern."""
        string_nodes = _find_nodes_by_type(root, "string")
        matches: list[URIMatch] = []
        for node in string_nodes:
            text = _node_text(node, source)
            cleaned = text.strip("\"'`")
            if URI_PATH_RE.match(cleaned):
                matches.append(URIMatch(uri=cleaned, source_file=file_path))
        return matches

    def _extract_functions(
        self, root: Node, source: bytes, file_path: str, lang: str
    ) -> list[FunctionDef]:
        """Extract function/method definitions with name, line, and snippet."""
        func_type = self._function_node_type(lang)
        func_nodes = _find_nodes_by_type(root, func_type)
        results: list[FunctionDef] = []

        for node in func_nodes:
            name = self._extract_function_name(node, source, lang)
            if name is None:
                continue
            snippet = _node_text(node, source)
            results.append(
                FunctionDef(
                    name=name,
                    file_path=file_path,
                    line=node.start_point[0] + 1,
                    snippet=snippet[:512],
                )
            )
        return results

    def _extract_calls(
        self, root: Node, source: bytes, file_path: str, lang: str
    ) -> list[CallEdge]:
        """Extract internal call relationships (caller → callee)."""
        call_type = "call"
        call_nodes = _find_nodes_by_type(root, call_type)
        results: list[CallEdge] = []

        for node in call_nodes:
            callee = self._extract_callee_name(node, source, lang)
            if callee is None:
                continue
            caller = self._find_enclosing_function(node, source, lang)
            if caller is None:
                continue
            results.append(
                CallEdge(
                    caller=f"{file_path}::{caller}",
                    callee=callee,
                    call_type="internal",
                    line=node.start_point[0] + 1,
                )
            )
        return results

    @staticmethod
    def _function_node_type(lang: str) -> str:
        """Return the tree-sitter node type for function definitions."""
        return {
            "python": "function_definition",
            "java": "method_declaration",
            "typescript": "function_declaration",
            "tsx": "function_declaration",
        }.get(lang, "function_definition")

    @staticmethod
    def _extract_function_name(node: Node, source: bytes, lang: str) -> str | None:
        """Extract the name of a function/method from its definition node."""
        name_field = {
            "python": "name",
            "java": "name",
            "typescript": "name",
            "tsx": "name",
        }.get(lang, "name")

        name_node = node.child_by_field_name(name_field)
        if name_node is not None:
            return _node_text(name_node, source)

        for child in node.children:
            if child.type == "identifier":
                return _node_text(child, source)
        return None

    @staticmethod
    def _extract_callee_name(node: Node, source: bytes, lang: str) -> str | None:
        """Extract the callee name from a call expression node."""
        func_node = node.child_by_field_name("function")
        if func_node is not None:
            return _node_text(func_node, source)

        for child in node.children:
            if child.type == "identifier":
                return _node_text(child, source)
            if child.type == "property_identifier":
                return _node_text(child, source)
            if child.type == "member_expression":
                return _node_text(child, source)
        return None

    @staticmethod
    def _find_enclosing_function(node: Node, source: bytes, lang: str) -> str | None:
        """Walk up the tree to find the enclosing function definition."""
        func_type = TreeSitterParser._function_node_type(lang)
        current = node.parent
        while current is not None:
            if current.type == func_type:
                name_field = "name"
                name_node = current.child_by_field_name(name_field)
                if name_node is not None:
                    return _node_text(name_node, source)
                for child in current.children:
                    if child.type == "identifier":
                        return _node_text(child, source)
            current = current.parent
        return None
