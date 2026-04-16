"""Tree-sitter based semantic parser for Python, TypeScript, and Java.

Extracts string literals, function definitions (with line numbers & snippets),
internal and external call graphs, endpoint-implementation mappings,
and HTTP client calls to external endpoints.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

import tree_sitter_java as tsjava
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

from bridgetrace.models.graph import (
    CallEdge,
    EndpointImpl,
    FunctionDef,
    HttpCall,
    ImportMapping,
    ParseResult,
    URIMatch,
    URIVariableDef,
)
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

_CALL_NODE_TYPES: dict[str, list[str]] = {
    "python": ["call"],
    "java": ["method_invocation"],
    "typescript": ["call_expression"],
    "tsx": ["call_expression"],
}

_HTTP_CLIENT_METHODS: dict[str, str] = {
    "getforobject": "GET",
    "postforobject": "POST",
    "putforobject": "PUT",
    "deleteforobject": "DELETE",
    "exchange": "GET",
    "execute": "GET",
    "fetch": "GET",
    "request": "GET",
}

_SIMPLE_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")


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


def _find_nodes_by_types(root: Node, type_names: set[str]) -> list[Node]:
    """Walk the tree and collect all nodes matching any of the given types."""
    results: list[Node] = []

    def _walk(node: Node) -> None:
        if node.type in type_names:
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

        functions = self._extract_functions(root, source, normalized_path, lang)

        func_by_name: dict[str, tuple[str, int]] = {}
        for func in functions:
            func_by_name[func.name] = (func.name, func.line)

        endpoint_impls = self._extract_endpoint_impls(root, source, normalized_path, lang)
        impl_uris: set[str] = set()
        for impl in endpoint_impls:
            impl_uris.add(impl.uri)

        uri_vars = self._extract_uri_variables(root, source, normalized_path, lang)

        var_to_uri: dict[str, str] = {}
        for uv in uri_vars:
            var_to_uri[uv.name] = uv.uri

        http_calls = self._extract_http_calls(root, source, normalized_path, lang, var_to_uri)
        http_call_uris: set[str] = set()
        for hc in http_calls:
            http_call_uris.add(hc.uri)

        uris = self._extract_uri_literals(root, source, normalized_path, impl_uris, http_call_uris)
        calls = self._extract_calls(root, source, normalized_path, lang, func_by_name)

        imports = self._extract_imports(root, source, normalized_path, lang)

        return ParseResult(
            file_path=normalized_path,
            uris=uris,
            functions=functions,
            calls=calls,
            endpoint_impls=endpoint_impls,
            http_calls=http_calls,
            uri_vars=uri_vars,
            imports=imports,
        )

    def _extract_uri_literals(
        self,
        root: Node,
        source: bytes,
        file_path: str,
        impl_uris: set[str],
        http_call_uris: set[str],
    ) -> list[URIMatch]:
        """Extract string literals that match the URI path pattern."""
        string_nodes = _find_nodes_by_types(root, {"string", "string_literal"})
        matches: list[URIMatch] = []
        for node in string_nodes:
            text = _node_text(node, source)
            cleaned = text.strip("\"'`")
            if not URI_PATH_RE.match(cleaned):
                continue
            if cleaned in impl_uris:
                matches.append(URIMatch(uri=cleaned, source_file=file_path, role="implementation"))
            elif cleaned in http_call_uris:
                matches.append(URIMatch(uri=cleaned, source_file=file_path, role="reference"))
            else:
                matches.append(URIMatch(uri=cleaned, source_file=file_path, role="reference"))
        return matches

    def _extract_functions(
        self, root: Node, source: bytes, file_path: str, lang: str
    ) -> list[FunctionDef]:
        """Extract function/method definitions with name, line, and snippet."""
        func_types = self._function_node_types(lang)
        func_nodes = _find_nodes_by_types(root, func_types)
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
        """Extract call relationships. Same-file calls are internal, others are external."""
        call_types = set(_CALL_NODE_TYPES.get(lang, ["call"]))
        call_nodes = _find_nodes_by_types(root, call_types)
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
            else:
                results.append(
                    CallEdge(
                        caller=caller_key,
                        callee=callee_name,
                        call_type="external",
                        line=node.start_point[0] + 1,
                    )
                )
        return results

    def _extract_endpoint_impls(
        self, root: Node, source: bytes, file_path: str, lang: str
    ) -> list[EndpointImpl]:
        """Extract endpoint-implementation mappings from annotations/decorators."""
        func_types = self._function_node_types(lang)
        func_nodes = _find_nodes_by_types(root, func_types)
        results: list[EndpointImpl] = []

        if lang == "java":
            return self._extract_java_endpoint_impls(root, source, file_path, func_nodes)

        for func_node in func_nodes:
            name = self._extract_function_name(func_node, source, lang)
            if name is None:
                continue
            line = func_node.start_point[0] + 1
            annotation_entries = self._find_annotation_uris(func_node, source, lang)
            for uri, http_method in annotation_entries:
                results.append(
                    EndpointImpl(
                        uri=uri,
                        function_name=name,
                        function_line=line,
                        http_method=http_method,
                    )
                )
        return results

    def _extract_java_endpoint_impls(
        self,
        root: Node,
        source: bytes,
        file_path: str,
        func_nodes: list[Node],
    ) -> list[EndpointImpl]:
        """Extract endpoint implementations for Java with class-level @RequestMapping support."""
        results: list[EndpointImpl] = []

        class_to_prefix: dict[str, str] = {}
        interface_to_prefix: dict[str, str] = {}

        for decl_type, prefix_map in (
            ("class_declaration", class_to_prefix),
            ("interface_declaration", interface_to_prefix),
        ):
            for node in _find_nodes_by_type(root, decl_type):
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                class_name = _node_text(name_node, source)
                prefix = self._extract_java_class_level_request_mapping(node, source)
                if prefix:
                    prefix_map[class_name] = prefix

        for func_node in func_nodes:
            name = self._extract_function_name(func_node, source, "java")
            if name is None:
                continue
            line = func_node.start_point[0] + 1

            entries = self._find_java_method_annotations(func_node, source)

            if not entries:
                continue

            containing_class = self._find_containing_class(func_node, source)
            containing_interface = self._find_containing_interface(func_node, source)

            if containing_class:
                base_prefix = class_to_prefix.get(containing_class, "")
            elif containing_interface:
                base_prefix = interface_to_prefix.get(containing_interface, "")
            else:
                base_prefix = ""

            for uri, http_method in entries:
                full_uri = self._merge_uri(base_prefix, uri)
                results.append(
                    EndpointImpl(
                        uri=full_uri,
                        function_name=name,
                        function_line=line,
                        http_method=http_method,
                    )
                )

        return results

    @staticmethod
    def _find_containing_class(node: Node, source: bytes) -> str | None:
        """Find the name of the containing class, if any."""
        current = node.parent
        while current is not None:
            if current.type == "class_declaration":
                name_node = current.child_by_field_name("name")
                if name_node:
                    return _node_text(name_node, source)
                return None
            if current.type == "interface_declaration":
                return None
            current = current.parent
        return None

    @staticmethod
    def _find_containing_interface(node: Node, source: bytes) -> str | None:
        """Find the name of the containing interface, if any."""
        current = node.parent
        while current is not None:
            if current.type == "interface_declaration":
                name_node = current.child_by_field_name("name")
                if name_node:
                    return _node_text(name_node, source)
                return None
            if current.type == "class_declaration":
                return None
            current = current.parent
        return None

    def _extract_java_class_level_request_mapping(self, node: Node, source: bytes) -> str:
        """Extract URI prefix from class/interface level @RequestMapping."""
        modifiers = self._get_modifiers_node(node)
        if modifiers is None:
            return ""

        for child in modifiers.children:
            if child.type == "annotation":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                ann_name = _node_text(name_node, source)
                if ann_name == "RequestMapping":
                    return self._extract_uri_from_annotation(child, source) or ""
        return ""

    def _find_java_method_annotations(
        self, func_node: Node, source: bytes
    ) -> list[tuple[str, str]]:
        """Extract URI and HTTP method from Java method annotations.

        Handles both annotation with args and marker_annotation without args.
        """
        results: list[tuple[str, str]] = []
        seen: set[str] = set()

        modifiers = self._get_modifiers_node(func_node)
        if modifiers is None:
            return results

        for child in modifiers.children:
            if child.type == "annotation":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                ann_name = _node_text(name_node, source)
                http_method = self._ANNOTATION_HTTP_MAP.get(ann_name, "")
                uri = self._extract_uri_from_annotation(child, source)
                if uri is None:
                    uri = ""
                if uri not in seen:
                    seen.add(uri)
                    results.append((uri, http_method))
            elif child.type == "marker_annotation":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                ann_name = _node_text(name_node, source)
                http_method = self._ANNOTATION_HTTP_MAP.get(ann_name, "")
                if http_method:
                    uri = ""
                    if uri not in seen:
                        seen.add(uri)
                        results.append((uri, http_method))

        return results

    def _extract_uri_from_annotation(self, node: Node, source: bytes) -> str | None:
        """Extract URI string from a Java annotation's argument list."""
        for child in node.children:
            if child.type == "annotation_argument_list":
                for string_node in _find_nodes_by_type(child, "string_literal"):
                    text = _node_text(string_node, source)
                    cleaned = text.strip("\"'")
                    if URI_PATH_RE.match(cleaned):
                        return cleaned
        return None

    @staticmethod
    def _get_modifiers_node(node: Node) -> Node | None:
        """Get modifiers node, handling tree-sitter field name quirks."""
        modifiers = node.child_by_field_name("modifiers")
        if modifiers is not None:
            return modifiers
        for child in node.children:
            if child.type == "modifiers":
                return child
        return None

    @staticmethod
    def _merge_uri(prefix: str, uri: str) -> str:
        """Merge class-level prefix with method-level URI."""
        if not prefix:
            return uri
        if not uri:
            return prefix
        prefix = prefix.rstrip("/")
        if not uri.startswith("/"):
            return f"{prefix}/{uri}"
        return prefix + uri

    def _extract_http_calls(
        self,
        root: Node,
        source: bytes,
        file_path: str,
        lang: str,
        var_to_uri: dict[str, str],
    ) -> list[HttpCall]:
        """Extract HTTP client calls that target URI endpoints."""
        call_types = set(_CALL_NODE_TYPES.get(lang, ["call"]))
        call_nodes = _find_nodes_by_types(root, call_types)
        results: list[HttpCall] = []

        for node in call_nodes:
            callee_name = self._extract_callee_name(node, source, lang)
            if callee_name is None:
                continue
            method_name = _normalize_http_method(callee_name)
            if method_name is None:
                continue

            caller_info = self._find_enclosing_function(node, source, lang)
            if caller_info is None:
                continue
            caller_name, caller_line = caller_info
            caller_key = f"{file_path}::{caller_name}:{caller_line}"

            uri = self._find_uri_in_call_args(node, source)
            var_ref = ""
            if uri is None:
                uri, var_ref = self._extract_uri_or_var_ref(node, source, lang, var_to_uri)

            if uri or var_ref:
                results.append(
                    HttpCall(
                        caller=caller_key,
                        uri=uri or "",
                        http_method=method_name,
                        line=node.start_point[0] + 1,
                        var_ref=var_ref,
                    )
                )
        return results

    _ANNOTATION_HTTP_MAP: ClassVar[dict[str, str]] = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "DeleteMapping": "DELETE",
        "PatchMapping": "PATCH",
        "RequestMapping": "",
        "get": "GET",
        "post": "POST",
        "put": "PUT",
        "delete": "DELETE",
        "patch": "PATCH",
        "Get": "GET",
        "Post": "POST",
        "Put": "PUT",
        "Delete": "DELETE",
        "Patch": "PATCH",
    }

    def _find_annotation_uris(
        self, func_node: Node, source: bytes, lang: str
    ) -> list[tuple[str, str]]:
        """Find URI strings in annotations/decorators attached to a function.

        Returns list of (uri, http_method) tuples.
        """
        results: list[tuple[str, str]] = []
        seen: set[str] = set()
        search_nodes: list[tuple[Node, str]] = []

        if lang == "java":
            for child in func_node.children:
                if child.type in ("annotation", "marker_annotation", "modifier"):
                    http_method = self._extract_http_method_from_annotation(child, source)
                    search_nodes.append((child, http_method))
                elif child.type == "modifiers":
                    for mod_child in child.children:
                        if mod_child.type in ("annotation", "marker_annotation"):
                            http_method = self._extract_http_method_from_annotation(
                                mod_child, source
                            )
                            search_nodes.append((mod_child, http_method))
        elif lang == "python":
            parent = func_node.parent
            if parent is not None and parent.type == "decorated_definition":
                for child in parent.children:
                    if child.type == "decorator":
                        http_method = self._extract_http_method_from_decorator(child, source)
                        search_nodes.append((child, http_method))
        elif lang in ("typescript", "tsx"):
            for child in func_node.children:
                if child.type == "decorator":
                    http_method = self._extract_http_method_from_decorator(child, source)
                    search_nodes.append((child, http_method))
            parent = func_node.parent
            if parent is not None:
                for child in parent.children:
                    if (
                        child.type == "decorator"
                        and child.start_point[0] < func_node.start_point[0]
                    ):
                        http_method = self._extract_http_method_from_decorator(child, source)
                        search_nodes.append((child, http_method))

        for search_node, http_method in search_nodes:
            for string_type in ("string", "string_literal"):
                for string_node in _find_nodes_by_type(search_node, string_type):
                    text = _node_text(string_node, source)
                    cleaned = text.strip("\"'`")
                    if URI_PATH_RE.match(cleaned) and cleaned not in seen:
                        seen.add(cleaned)
                        results.append((cleaned, http_method))

        return results

    @staticmethod
    def _extract_http_method_from_annotation(node: Node, source: bytes) -> str:
        """Extract HTTP method from a Java annotation node name."""
        text = _node_text(node, source)
        for ann_name, method in TreeSitterParser._ANNOTATION_HTTP_MAP.items():
            if ann_name in text:
                return method
        return ""

    @staticmethod
    def _extract_http_method_from_decorator(node: Node, source: bytes) -> str:
        """Extract HTTP method from a Python/TS decorator node."""
        text = _node_text(node, source)
        for dec_name, method in TreeSitterParser._ANNOTATION_HTTP_MAP.items():
            if dec_name in text:
                return method
        return ""

    @staticmethod
    def _find_uri_in_call_args(node: Node, source: bytes) -> str | None:
        """Find a URI string in the arguments of a call expression."""
        for child in node.children:
            if child.type in ("argument_list", "arguments"):
                for arg in child.children:
                    if arg.type in ("string", "string_literal"):
                        text = _node_text(arg, source)
                        cleaned = text.strip("\"'`")
                        if URI_PATH_RE.match(cleaned):
                            return cleaned
        return None

    @staticmethod
    def _function_node_types(lang: str) -> set[str]:
        """Return the tree-sitter node types for function/method definitions."""
        base: dict[str, set[str]] = {
            "python": {"function_definition"},
            "java": {"method_declaration", "constructor_declaration", "lambda_expression"},
            "typescript": {"function_declaration", "method_definition"},
            "tsx": {"function_declaration", "method_definition"},
        }
        return base.get(lang, {"function_definition"})

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
        # Java method_invocation uses "name" field for the method identifier
        if lang == "java":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                obj_node = node.child_by_field_name("object")
                method = _node_text(name_node, source)
                if obj_node is not None:
                    obj = _node_text(obj_node, source)
                    return f"{obj}.{method}"
                return method

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
        """Walk up the tree to find the enclosing function definition."""
        func_types = TreeSitterParser._function_node_types(lang)

        current = node.parent
        while current is not None:
            if current.type in func_types:
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

    def _extract_imports(
        self, root: Node, source: bytes, file_path: str, lang: str
    ) -> list[ImportMapping]:
        """Extract import statements for cross-file variable resolution."""
        results: list[ImportMapping] = []

        if lang in ("typescript", "tsx"):
            results.extend(self._extract_ts_imports(root, source, file_path))
        elif lang == "python":
            results.extend(self._extract_py_imports(root, source, file_path))

        return results

    def _extract_ts_imports(self, root: Node, source: bytes, file_path: str) -> list[ImportMapping]:
        """Extract import statements from TypeScript/TSX files."""
        results: list[ImportMapping] = []

        import_stmts = _find_nodes_by_type(root, "import_statement")
        for stmt in import_stmts:
            source_node = None
            for child in stmt.children:
                if child.type == "string":
                    source_node = child
                    break

            if source_node is None:
                continue

            source_file = _node_text(source_node, source).strip("\"'")

            for child in stmt.children:
                if child.type == "import_clause":
                    for ic in child.children:
                        if ic.type == "named_imports":
                            for ni in ic.children:
                                if ni.type == "import_specifier":
                                    name_node = ni.child_by_field_name("name")
                                    alias_node = ni.child_by_field_name("alias")
                                    if name_node:
                                        local_name = _node_text(
                                            alias_node if alias_node else name_node, source
                                        )
                                        source_name = _node_text(name_node, source)
                                        results.append(
                                            ImportMapping(
                                                local_name=local_name,
                                                source_name=source_name,
                                                source_file=source_file,
                                                file_path=file_path,
                                                line=stmt.start_point[0] + 1,
                                            )
                                        )
                        if ic.type == "identifier":
                            local_name = _node_text(ic, source)
                            results.append(
                                ImportMapping(
                                    local_name=local_name,
                                    source_name=local_name,
                                    source_file=source_file,
                                    file_path=file_path,
                                    line=stmt.start_point[0] + 1,
                                )
                            )
                        if ic.type == "namespace_import":
                            for ni in ic.children:
                                if ni.type == "identifier":
                                    local_name = _node_text(ni, source)
                                    results.append(
                                        ImportMapping(
                                            local_name=local_name,
                                            source_name="*",
                                            source_file=source_file,
                                            file_path=file_path,
                                            line=stmt.start_point[0] + 1,
                                        )
                                    )

        return results

    def _extract_py_imports(self, root: Node, source: bytes, file_path: str) -> list[ImportMapping]:
        """Extract import statements from Python files."""
        results: list[ImportMapping] = []

        import_stmts = _find_nodes_by_types(root, {"import_statement", "import_from_statement"})
        for stmt in import_stmts:
            if stmt.type == "import_statement":
                for child in stmt.children:
                    if child.type == "dotted_name":
                        module = _node_text(child, source)
                        parts = module.split(".")
                        local_name = parts[-1] if parts else module
                        results.append(
                            ImportMapping(
                                local_name=local_name,
                                source_name=module,
                                source_file=module,
                                file_path=file_path,
                                line=stmt.start_point[0] + 1,
                            )
                        )
                    if child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        alias_node = child.child_by_field_name("alias")
                        if name_node and alias_node:
                            source_name = _node_text(name_node, source)
                            local_name = _node_text(alias_node, source)
                            results.append(
                                ImportMapping(
                                    local_name=local_name,
                                    source_name=source_name,
                                    source_file=source_name,
                                    file_path=file_path,
                                    line=stmt.start_point[0] + 1,
                                )
                            )

            if stmt.type == "import_from_statement":
                module_name = ""
                first_dotted = True
                for child in stmt.children:
                    if child.type == "dotted_name":
                        if first_dotted:
                            module_name = _node_text(child, source)
                            first_dotted = False
                        else:
                            local_name = _node_text(child, source)
                            results.append(
                                ImportMapping(
                                    local_name=local_name,
                                    source_name=local_name,
                                    source_file=module_name,
                                    file_path=file_path,
                                    line=stmt.start_point[0] + 1,
                                )
                            )

                for child in stmt.children:
                    if child.type == "import_list":
                        for il in child.children:
                            if il.type == "identifier":
                                local_name = _node_text(il, source)
                                results.append(
                                    ImportMapping(
                                        local_name=local_name,
                                        source_name=local_name,
                                        source_file=module_name,
                                        file_path=file_path,
                                        line=stmt.start_point[0] + 1,
                                    )
                                )
                            if il.type == "aliased_import":
                                name_node = il.child_by_field_name("name")
                                alias_node = il.child_by_field_name("alias")
                                if name_node and alias_node:
                                    source_name = _node_text(name_node, source)
                                    local_name = _node_text(alias_node, source)
                                    results.append(
                                        ImportMapping(
                                            local_name=local_name,
                                            source_name=source_name,
                                            source_file=module_name,
                                            file_path=file_path,
                                            line=stmt.start_point[0] + 1,
                                        )
                                    )
                    if child.type == "identifier":
                        local_name = _node_text(child, source)
                        results.append(
                            ImportMapping(
                                local_name=local_name,
                                source_name=local_name,
                                source_file=module_name,
                                file_path=file_path,
                                line=stmt.start_point[0] + 1,
                            )
                        )

        return results

    def _extract_uri_variables(
        self, root: Node, source: bytes, file_path: str, lang: str
    ) -> list[URIVariableDef]:
        """Extract variable definitions that hold URI values."""
        results: list[URIVariableDef] = []

        if lang not in ("typescript", "tsx", "python"):
            return results

        if lang in ("typescript", "tsx"):
            results.extend(self._extract_ts_uri_variables(root, source, file_path))
        elif lang == "python":
            results.extend(self._extract_py_uri_variables(root, source, file_path))

        return results

    def _extract_ts_uri_variables(
        self, root: Node, source: bytes, file_path: str
    ) -> list[URIVariableDef]:
        """Extract URI variable definitions from TypeScript/TSX files."""
        results: list[URIVariableDef] = []
        seen_names: set[str] = set()

        def is_exported_node(node: Node) -> bool:
            current = node.parent
            while current:
                if current.type == "export_statement":
                    return True
                if current.type in ("function_declaration", "class_declaration"):
                    break
                current = current.parent
            return False

        def process_declarator(decl_node: Node) -> None:
            name_node = decl_node.child_by_field_name("name")
            value_node = decl_node.child_by_field_name("value")
            if name_node is None or value_node is None:
                return

            name = self._extract_var_name(name_node, source)
            if name is None:
                return

            is_exported = is_exported_node(decl_node)

            if value_node.type in ("object", "object_expression"):
                for child in value_node.children:
                    if child.type == "pair":
                        key_node = child.child_by_field_name("key")
                        val_node = child.child_by_field_name("value")
                        if key_node and val_node:
                            prop_name = _node_text(key_node, source).strip("\"'")
                            full_name = f"{name}.{prop_name}"
                            if full_name in seen_names:
                                continue
                            uri = self._extract_uri_from_value(val_node, source)
                            if uri:
                                seen_names.add(full_name)
                                results.append(
                                    URIVariableDef(
                                        name=full_name,
                                        uri=uri,
                                        file_path=file_path,
                                        line=decl_node.start_point[0] + 1,
                                        is_exported=is_exported,
                                    )
                                )
            else:
                if name in seen_names:
                    return
                uri = self._extract_uri_from_value(value_node, source)
                if uri:
                    seen_names.add(name)
                    results.append(
                        URIVariableDef(
                            name=name,
                            uri=uri,
                            file_path=file_path,
                            line=decl_node.start_point[0] + 1,
                            is_exported=is_exported,
                        )
                    )

        variable_declarators = _find_nodes_by_type(root, "variable_declarator")
        for decl in variable_declarators:
            process_declarator(decl)

        return results

    def _extract_py_uri_variables(
        self, root: Node, source: bytes, file_path: str
    ) -> list[URIVariableDef]:
        """Extract URI variable definitions from Python files."""
        results: list[URIVariableDef] = []
        seen: set[str] = set()

        assignments = _find_nodes_by_type(root, "assignment")
        for asn in assignments:
            left = asn.child_by_field_name("left")
            right = asn.child_by_field_name("right")
            if left is None or right is None:
                continue

            if left.type == "identifier":
                name = _node_text(left, source)
            else:
                continue

            uri = self._extract_uri_from_value(right, source)
            if uri and uri not in seen:
                seen.add(uri)
                results.append(
                    URIVariableDef(
                        name=name,
                        uri=uri,
                        file_path=file_path,
                        line=asn.start_point[0] + 1,
                    )
                )

        return results

    @staticmethod
    def _extract_var_name(node: Node, source: bytes) -> str | None:
        """Extract the variable name from a name node (identifier or pattern)."""
        if node.type == "identifier":
            return _node_text(node, source)
        if node.type == "object_pattern":
            for child in node.children:
                if child.type == "shorthand_property_identifier":
                    return _node_text(child, source)
                if child.type == "pair":
                    key = child.child_by_field_name("key")
                    if key:
                        return _node_text(key, source)
        return None

    def _extract_uri_from_value(self, node: Node, source: bytes) -> str | None:
        """Extract URI from a value node (string literal, object property, etc.)."""
        if node.type in ("string", "string_literal"):
            text = _node_text(node, source)
            cleaned = text.strip("\"'`")
            if URI_PATH_RE.match(cleaned):
                return cleaned
            return None

        if node.type in ("object", "object_expression"):
            uri = self._find_uri_in_object(node, source)
            if uri:
                return uri

        if node.type == "template_string":
            return self._extract_uri_from_template(node, source)

        return None

    def _find_uri_in_object(self, node: Node, source: bytes) -> str | None:
        """Find a URI value in an object literal."""
        for child in node.children:
            if child.type == "pair":
                key_node = child.child_by_field_name("key")
                value_node = child.child_by_field_name("value")
                if key_node and value_node:
                    _node_text(key_node, source).strip("\"'")
                    value = self._extract_uri_from_value(value_node, source)
                    if value:
                        return value
            if child.type == "spread_element":
                continue
        return None

    def _extract_uri_from_template(self, node: Node, source: bytes) -> str | None:
        """Extract URI from a template string, simplifying variables to {}."""
        parts: list[str] = []
        for child in node.children:
            if child.type == "string_fragment":
                parts.append(_node_text(child, source))
            elif child.type == "template_substitution":
                parts.append("{}")
        result = "".join(parts)
        if result.startswith("/") and len(result) > 1:
            normalized = re.sub(r"\{[^}]*\}", "{}", result)
            return normalized
        return None

    def _resolve_uri_from_variable(
        self, node: Node, source: bytes, lang: str, var_to_uri: dict[str, str]
    ) -> str | None:
        """Try to resolve a URI from a variable reference in call arguments."""
        for child in node.children:
            if child.type in ("argument_list", "arguments"):
                for arg in child.children:
                    if arg.type in ("identifier", "member_expression"):
                        var_name = _node_text(arg, source)
                        if var_name in var_to_uri:
                            return var_to_uri[var_name]
                    if arg.type == "template_string":
                        return self._extract_uri_from_template(arg, source)
        return None

    def _extract_uri_or_var_ref(
        self, node: Node, source: bytes, lang: str, var_to_uri: dict[str, str]
    ) -> tuple[str | None, str]:
        """Extract URI or variable reference from HTTP call arguments.

        Returns (uri, var_ref) tuple. If URI can be resolved locally, returns it.
        Otherwise returns the variable reference for cross-file resolution.
        """
        for child in node.children:
            if child.type in ("argument_list", "arguments"):
                for arg in child.children:
                    if arg.type in ("identifier", "member_expression"):
                        var_name = _node_text(arg, source)
                        if var_name in var_to_uri:
                            return var_to_uri[var_name], var_name
                        return None, var_name
                    if arg.type == "template_string":
                        uri = self._extract_uri_from_template(arg, source)
                        return uri, ""
        return None, ""


def _normalize_http_method(callee_name: str) -> str | None:
    """Extract the HTTP method from a callee name, if it is an HTTP client call."""
    if "." in callee_name:
        last_segment = callee_name.rsplit(".", 1)[-1].lower()
    else:
        last_segment = callee_name.lower()

    if last_segment in _HTTP_CLIENT_METHODS:
        return _HTTP_CLIENT_METHODS[last_segment]

    for method in _SIMPLE_HTTP_METHODS:
        if last_segment == method:
            return method.upper()

    return None
