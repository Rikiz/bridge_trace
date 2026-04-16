from __future__ import annotations

from pathlib import Path

from bridgetrace.core.scanner import (
    _PARAM_RE,
    _compute_route_score,
    _extract_subpath_keys,
    _uri_reverse_match,
    _uri_suffix_match,
)
from bridgetrace.core.scanner import Scanner


class TestParamRE:
    def test_dollar_braces(self):
        assert _PARAM_RE.search("/data/v1/users/${id}")

    def test_plain_braces(self):
        assert _PARAM_RE.search("/v1/users/{id}")

    def test_no_params(self):
        assert _PARAM_RE.search("/v1/users") is None

    def test_sub_to_placeholder(self):
        result = _PARAM_RE.sub("{}", "/data/v1/users/${id}")
        assert result == "/data/v1/users/{}"


class TestUriSuffixMatch:
    def test_standard_match(self):
        assert _uri_suffix_match("/data/v1/users/{id}", "/v1/users/{id}")

    def test_no_match(self):
        assert not _uri_suffix_match("/data/v1/users", "/v1/orders")

    def test_dollar_vs_plain_brace(self):
        assert _uri_suffix_match("/data/v1/tenant-configs/${id}", "/v1/tenant-configs/{id}")

    def test_same_uri(self):
        assert _uri_suffix_match("/v1/users", "/v1/users")

    def test_impl_too_short(self):
        assert not _uri_suffix_match("/data/v1/users", "/users")

    def test_decl_shorter_than_impl(self):
        assert not _uri_suffix_match("/users", "/v1/users")

    def test_multiple_segments(self):
        assert _uri_suffix_match("/api/gateway/v2/items/{sku}", "/v2/items/{sku}")


class TestUriReverseMatch:
    def test_basic_reverse_match(self):
        # /api/users/{id} 与 v1/rest/api/users/{id} 应该匹配
        assert _uri_reverse_match("/api/users/{id}", "v1/rest/api/users/{id}")
        # /rest/api/proc/{id} 与 v1/rest/api/users/{id} 不应该匹配
        assert not _uri_reverse_match("/rest/api/proc/{id}", "v1/rest/api/users/{id}")

    def test_min_segments(self):
        # 只匹配1个片段，但较短URL的所有非参数片段都匹配，所以匹配
        assert _uri_reverse_match("/api/users", "/users")
        # 匹配2个片段，应该匹配
        assert _uri_reverse_match("/api/v1/users", "/v1/users")
        # 匹配3个片段，当然匹配
        assert _uri_reverse_match("/data/api/v1/users", "x/data/api/v1/users")

    def test_exhausted_match(self):
        # 当一个URL的所有非参数片段都匹配时（即使<min_segments）
        # /users/{id} 的非参数片段只有 ["users"]，全部匹配
        assert _uri_reverse_match("/users/{id}", "api/users/{id}")
        # 反向测试
        assert _uri_reverse_match("api/users/{id}", "/users/{id}")

    def test_parameter_normalization(self):
        # 参数归一化测试
        assert _uri_reverse_match("/api/users/${id}", "/api/users/{id}")
        assert _uri_reverse_match("/api/users/{userId}", "v1/api/users/{id}")
        # 参数位置不同不匹配
        assert not _uri_reverse_match("/api/{id}/users", "/api/users/{id}")

    def test_slash_handling(self):
        # 自动补全前导斜杠
        assert _uri_reverse_match("api/users/{id}", "/v1/api/users/{id}")
        assert _uri_reverse_match("/api/users/{id}", "v1/api/users/{id}")
        # 尾部斜杠处理
        assert _uri_reverse_match("/api/users/{id}/", "v1/api/users/{id}")

    def test_edge_cases(self):
        # 空URL或纯参数URL
        assert not _uri_reverse_match("/{id}", "/users/{id}")
        assert not _uri_reverse_match("/{a}/{b}", "/x/y/z")
        # 只有一个非参数片段且不匹配
        assert not _uri_reverse_match("/api", "/users")
        # 完全相同的URL
        assert _uri_reverse_match("/api/users/{id}", "/api/users/{id}")
        # 顺序不影响匹配
        assert _uri_reverse_match("v1/rest/api/users/{id}", "/api/users/{id}")


class TestSubpathKeys:
    def test_basic_uri(self):
        keys = _extract_subpath_keys("/v1/users/{id}")
        assert "v1/users/{}" in keys

    def test_long_uri(self):
        keys = _extract_subpath_keys("/data/v1/tenant-configs/${id}")
        assert "data/v1/tenant-configs/{}" in keys
        assert "v1/tenant-configs/{}" in keys

    def test_shared_subpath(self):
        keys_a = set(_extract_subpath_keys("/data/v1/tenant-configs/${id}"))
        keys_b = set(_extract_subpath_keys("/rest/v1/tenant-configs/{id}"))
        assert keys_a & keys_b, "Should share at least one subpath key"

    def test_short_uri_no_keys(self):
        keys = _extract_subpath_keys("/users")
        assert keys == []


class TestComputeRouteScore:
    def test_both_methods_match(self):
        score = _compute_route_score(3, "DELETE", "DELETE", False)
        assert score == 8  # 3 depth + 5 method match

    def test_methods_conflict(self):
        score = _compute_route_score(3, "GET", "DELETE", False)
        assert score < 0

    def test_one_method_unknown(self):
        score = _compute_route_score(3, "DELETE", "", False)
        assert score == 4  # 3 depth + 1 one unknown

    def test_same_file_penalty(self):
        score = _compute_route_score(3, "GET", "GET", True)
        assert score == 5  # 3 depth + 5 method - 3 same file

    def test_both_unknown(self):
        score = _compute_route_score(2, "", "", False)
        assert score == 3


class TestCrossFileVariableResolution:
    def test_resolve_imported_uri_variable(self, tmp_path):
        constants_file = tmp_path / "constants.ts"
        constants_file.write_text(
            "export const URLS = {\n"
            '  USERS: "/api/v1/users",\n'
            '  ORDERS: "/api/v1/orders",\n'
            "};\n",
            encoding="utf-8",
        )

        service_file = tmp_path / "service.ts"
        service_file.write_text(
            'import { URLS } from "./constants";\n'
            'import axios from "axios";\n'
            "async function fetchUsers() {\n"
            "    return axios.get(URLS.USERS);\n"
            "}\n",
            encoding="utf-8",
        )

        scanner = Scanner()
        results = scanner.scan_paths([tmp_path])
        nodes, edges = scanner.build_graph_entities(results, "test_group", [tmp_path])

        consumes_edges = [e for e in edges if e.rel_type == "CONSUMES"]
        assert len(consumes_edges) == 1
        assert consumes_edges[0].properties.get("http_method") == "GET"

        endpoint_nodes = [n for n in nodes if n.label == "Endpoint"]
        users_endpoint = next(
            (n for n in endpoint_nodes if n.properties.get("uri") == "/api/v1/users"), None
        )
        assert users_endpoint is not None
