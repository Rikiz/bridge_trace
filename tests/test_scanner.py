from __future__ import annotations

from bridgetrace.core.scanner import _PARAM_RE, _uri_suffix_match


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
