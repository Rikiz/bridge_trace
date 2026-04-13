"""Tree-sitter based semantic parser for Python, TypeScript, and Java.

Extracts string literals, function definitions (with line numbers & snippets),
and internal call graphs.

Uses the new tree-sitter 0.22+ API with individual language packages
(tree-sitter-python, tree-sitter-java, tree-sitter-typescript) instead
of the incompatible tree-sitter-languages bundle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tree_sitter_java as tsjava
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

from bridgetrace.models.graph import CallEdge, FunctionDef, ParseResult, URIMatch
from bridgetrace.parsers.base import BaseParser
from bridgetrace.parsers.json_parser import URI_PATH_RE
from bridgetrace.utils import normalize_path

_LANG_ENTRY_MAP: dict[str, Any] = {
    ".py": tspython,
    ".ts": tstypescript,
    ".tsx": tstypescript,
    ".java": tsjava,
}

_LANG_NAME_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
}


def _get_language_name(path: Path) -> str | None:
    """Return tree-sitter language name for a file extension."""
    return _LANG_NAME_MAP.get(path.suffix)


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

    supported_extensions = tuple(_LANG_NAME_MAP.keys())

    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}

    def _get_parser(self, ext: str) -> Parser:
        """Lazily initialise a tree-sitter parser for the given file extension."""
        if ext not in self._parsers:
            entry = _LANG_ENTRY_MAP[ext]
            # tree-sitter-typescript exposes language() and tsx_language()
            if ext == ".ts":
                lang = Language(entry.language_typescript())
            elif ext == ".tsx":
                lang = Language(entry.language_tsx())
            else:
                lang = Language(entry.language())
            parser = Parser(lang)
            self._parsers[ext] = parser
        return self._parsers[ext]

    def parse(self, path: Path) -> ParseResult:
        """Parse a source file and extract string literals, functions, and calls."""
        lang = _get_language_name(path)
        normalized_path = normalize_path(path)

        if lang is None:
            return ParseResult(file_path=normalized_path, uris=[], functions=[], calls=[])

        source = path.read_bytes()
        parser = self._get_parser(path.suffix)
        tree = parser.parse(source)
        root = tree.root_node

        uris = self._extract_uri_literals(root, source, normalized_path)
        functions = self._extract_functions(root, source, normalized_path, lang)

        func_by_name: dict[str, tuple[str, int]] = {}
        for func in functions:
            func_by_name[func.name] = (func.name, func.line)

        calls = self._extract_calls(root, source, normalized_path, lang, func_by_name)

        return ParseResult(
            file_path=normalized_path,
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
        self,
        root: Node,
        source: bytes,
        file_path: str,
        lang: str,
        func_by_name: dict[str, tuple[str, int]],
    ) -> list[CallEdge]:
        """Extract internal call relationships (caller -> callee)."""
        call_type = "call"
        call_nodes = _find_nodes_by_type(root, call_type)
        results: list[CallEdge] = []

        for node in call_nodes:
            callee_name = self._extract_callee_name(node, source, lang)
            if callee_name is None:
                continue
            caller_info = self._find_enclosing_function(node, source, lang)
            if caller_info is None:
                continue
            caller_name, caller_line = caller_info
            caller_key = f"{file_path}::{caller_name}:{caller_line}"

            if callee_name in func_by_name:
                callee_name_found, callee_line = func_by_name[callee_name]
                callee_key = f"{file_path}::{callee_name_found}:{callee_line}"
                results.append(
                    CallEdge(
                        caller=caller_key,
                        callee=callee_key,
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
    def _find_enclosing_function(node: Node, source: bytes, lang: str) -> tuple[str, int] | None:
        """Walk up the tree to find the enclosing function definition.

        Returns (function_name, line_number) or None.
        """
        func_type = TreeSitterParser._function_node_type(lang)
        current = node.parent
        while current is not None:
            if current.type == func_type:
                name_node = current.child_by_field_name("name")
                name = None
                if name_node is not None:
                    name = _node_text(name_node, source)
                else:
                    for child in current.children:
                        if child.type == "identifier":
                            name = _node_text(child, source)
                            break
                if name is not None:
                    line = current.start_point[0] + 1
                    return (name, line)
            current = current.parent
        return None
