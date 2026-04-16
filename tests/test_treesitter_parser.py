from __future__ import annotations

import tempfile
from pathlib import Path

from bridgetrace.parsers.treesitter_parser import TreeSitterParser


class TestTreeSitterParser:
    def test_can_parse_python(self, sample_python):
        parser = TreeSitterParser()
        assert parser.can_parse(sample_python)

    def test_can_parse_java(self, sample_java):
        parser = TreeSitterParser()
        assert parser.can_parse(sample_java)

    def test_can_parse_ts(self, sample_ts):
        parser = TreeSitterParser()
        assert parser.can_parse(sample_ts)

    def test_cannot_parse_json(self, sample_json):
        parser = TreeSitterParser()
        assert not parser.can_parse(sample_json)

    def test_parse_python_extracts_functions(self, sample_python):
        parser = TreeSitterParser()
        result = parser.parse(sample_python)
        func_names = [f.name for f in result.functions]
        assert "get_user" in func_names
        assert "create_user" in func_names

    def test_parse_python_extracts_calls(self, sample_python):
        parser = TreeSitterParser()
        result = parser.parse(sample_python)
        assert len(result.calls) > 0

    def test_parse_java_extracts_functions(self, sample_java):
        parser = TreeSitterParser()
        result = parser.parse(sample_java)
        func_names = [f.name for f in result.functions]
        assert "getUser" in func_names

    def test_parse_java_extracts_endpoint_impls(self, sample_java):
        parser = TreeSitterParser()
        result = parser.parse(sample_java)
        impl_uris = [impl.uri for impl in result.endpoint_impls]
        assert "/api/v1/users/{id}" in impl_uris

    def test_parse_ts_extracts_functions(self, sample_ts):
        parser = TreeSitterParser()
        result = parser.parse(sample_ts)
        func_names = [f.name for f in result.functions]
        assert "fetchUsers" in func_names

    def test_parse_empty_file(self, tmp_dir):
        p = tmp_dir / "empty.py"
        p.write_text("", encoding="utf-8")
        parser = TreeSitterParser()
        result = parser.parse(p)
        assert result.functions == []
        assert result.calls == []


class TestJavaInterfaceParsing:
    def test_extract_interface_endpoints(self, tmp_dir):
        p = tmp_dir / "UserApi.java"
        p.write_text(
            "package com.example.api;\n\n"
            "import org.springframework.web.bind.annotation.*;\n\n"
            '@RequestMapping("/api/v1/users")\n'
            "public interface UserApi {\n"
            '    @GetMapping("/{id}")\n'
            "    ResponseEntity<User> getUser(@PathVariable String id);\n"
            "    @PostMapping\n"
            "    ResponseEntity<User> createUser(@RequestBody UserRequest request);\n"
            "}\n",
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        impl_map = {impl.uri: impl for impl in result.endpoint_impls}
        assert "/api/v1/users/{id}" in impl_map
        assert impl_map["/api/v1/users/{id}"].http_method == "GET"
        assert impl_map["/api/v1/users/{id}"].function_name == "getUser"
        assert "/api/v1/users" in impl_map
        assert impl_map["/api/v1/users"].http_method == "POST"
        assert impl_map["/api/v1/users"].function_name == "createUser"

    def test_interface_with_impl_class(self, tmp_dir):
        p = tmp_dir / "OrderApi.java"
        p.write_text(
            "package com.example;\n\n"
            "import org.springframework.web.bind.annotation.*;\n\n"
            '@RequestMapping("/api/v1/orders")\n'
            "public interface OrderApi {\n"
            '    @GetMapping("/{orderId}")\n'
            "    Order getOrder(@PathVariable String orderId);\n"
            "}\n\n"
            "@RestController\n"
            "public class OrderApiController implements OrderApi {\n"
            "    @Override\n"
            "    public Order getOrder(String orderId) { return null; }\n"
            "}\n",
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        impl_uris = [impl.uri for impl in result.endpoint_impls]
        assert "/api/v1/orders/{orderId}" in impl_uris
        impl = next(i for i in result.endpoint_impls if i.uri == "/api/v1/orders/{orderId}")
        assert impl.http_method == "GET"

    def test_class_level_request_mapping_merged(self, tmp_dir):
        p = tmp_dir / "ProductController.java"
        p.write_text(
            "package com.example;\n\n"
            "import org.springframework.web.bind.annotation.*;\n\n"
            "@RestController\n"
            '@RequestMapping("/api/v1/products")\n'
            "public class ProductController {\n"
            "    @GetMapping\n"
            '    public String listProducts() { return "products"; }\n'
            '    @PostMapping("/create")\n'
            '    public String createProduct() { return "created"; }\n'
            "}\n",
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        impl_map = {impl.uri: impl for impl in result.endpoint_impls}
        assert "/api/v1/products" in impl_map
        assert impl_map["/api/v1/products"].http_method == "GET"
        assert "/api/v1/products/create" in impl_map
        assert impl_map["/api/v1/products/create"].http_method == "POST"


class TestURIVariables:
    def test_extract_ts_uri_variable_object(self, tmp_dir):
        p = tmp_dir / "urls.ts"
        p.write_text(
            "export const URLS = {\n"
            '  USERS: "/api/v1/users",\n'
            '  ORDERS: "/api/v1/orders",\n'
            "};\n",
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        var_names = [v.name for v in result.uri_vars]
        assert "URLS.USERS" in var_names
        assert "URLS.ORDERS" in var_names
        users_var = next(v for v in result.uri_vars if v.name == "URLS.USERS")
        assert users_var.uri == "/api/v1/users"
        assert users_var.is_exported is True

    def test_extract_ts_uri_variable_simple(self, tmp_dir):
        p = tmp_dir / "config.ts"
        p.write_text(
            'export const API_BASE = "/api/v1";\n' 'const INTERNAL_PATH = "/internal/users";\n',
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        exported = [v for v in result.uri_vars if v.is_exported]
        local = [v for v in result.uri_vars if not v.is_exported]
        assert any(v.name == "API_BASE" for v in exported)
        assert any(v.name == "INTERNAL_PATH" for v in local)

    def test_extract_py_uri_variable(self, tmp_dir):
        p = tmp_dir / "config.py"
        p.write_text(
            'USERS_URL = "/api/v1/users"\n' 'ORDERS_URL = "/api/v1/orders"\n',
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        var_names = [v.name for v in result.uri_vars]
        assert "USERS_URL" in var_names
        assert "ORDERS_URL" in var_names


class TestImportExtraction:
    def test_extract_ts_named_import(self, tmp_dir):
        p = tmp_dir / "service.ts"
        p.write_text(
            'import { URLS } from "./constants";\n' 'import axios from "axios";\n',
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        imports = {i.local_name: i for i in result.imports}
        assert "URLS" in imports
        assert imports["URLS"].source_name == "URLS"
        assert imports["URLS"].source_file == "./constants"

    def test_extract_ts_namespace_import(self, tmp_dir):
        p = tmp_dir / "service.ts"
        p.write_text(
            'import * as URLS from "./constants";\n',
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        imports = {i.local_name: i for i in result.imports}
        assert "URLS" in imports
        assert imports["URLS"].source_name == "*"

    def test_extract_ts_default_import(self, tmp_dir):
        p = tmp_dir / "service.ts"
        p.write_text(
            'import axios from "axios";\n',
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        imports = {i.local_name: i for i in result.imports}
        assert "axios" in imports

    def test_extract_py_import(self, tmp_dir):
        p = tmp_dir / "service.py"
        p.write_text(
            "from constants import URLS\n" "import requests\n",
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        imports = {i.local_name: i for i in result.imports}
        assert "URLS" in imports
        assert "requests" in imports


class TestHttpCallWithVarRef:
    def test_http_call_with_local_var(self, tmp_dir):
        p = tmp_dir / "service.ts"
        p.write_text(
            'import axios from "axios";\n'
            'const URLS = { USERS: "/api/v1/users" };\n'
            "async function fetchUsers() {\n"
            "    return axios.get(URLS.USERS);\n"
            "}\n",
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        assert len(result.http_calls) == 1
        hc = result.http_calls[0]
        assert hc.uri == "/api/v1/users"
        assert hc.var_ref == "URLS.USERS"

    def test_http_call_with_unresolved_var(self, tmp_dir):
        p = tmp_dir / "service.ts"
        p.write_text(
            'import axios from "axios";\n'
            "async function fetchUsers() {\n"
            "    return axios.get(URLS.USERS);\n"
            "}\n",
            encoding="utf-8",
        )
        parser = TreeSitterParser()
        result = parser.parse(p)
        assert len(result.http_calls) == 1
        hc = result.http_calls[0]
        assert hc.uri == ""
        assert hc.var_ref == "URLS.USERS"
