from __future__ import annotations

from bridgetrace.parsers.json_parser import URI_PATH_RE, JsonYamlParser


class TestURIPathRE:
    def test_simple_path(self):
        assert URI_PATH_RE.match("/api/v1/users")

    def test_trailing_slash_not_matched(self):
        assert URI_PATH_RE.match("/api/v1/users/") is None

    def test_single_segment(self):
        assert URI_PATH_RE.match("/api")

    def test_path_param_braces(self):
        assert URI_PATH_RE.match("/v1/tenant-configs/{id}")

    def test_path_param_dollar_braces(self):
        assert URI_PATH_RE.match("/data/v1/tenant-configs/${id}")

    def test_mixed_params(self):
        assert URI_PATH_RE.match("/api/v1/users/{userId}/orders/${orderId}")

    def test_no_leading_slash(self):
        assert URI_PATH_RE.match("api/v1/users") is None

    def test_empty_string(self):
        assert URI_PATH_RE.match("") is None

    def test_hyphen_and_dot(self):
        assert URI_PATH_RE.match("/v1/tenant-configs/v1.0")

    def test_nested_dollar_braces(self):
        assert URI_PATH_RE.match("/gateway/api/v2/items/${item.id}")


class TestJsonYamlParser:
    def test_parse_json(self, sample_json):
        parser = JsonYamlParser()
        assert parser.can_parse(sample_json)
        result = parser.parse(sample_json)
        uris = [u.uri for u in result.uris]
        assert "/api/v1/users" in uris
        assert "/data/v1/tenant-configs/${id}" in uris

    def test_parse_yaml(self, sample_yaml):
        parser = JsonYamlParser()
        assert parser.can_parse(sample_yaml)
        result = parser.parse(sample_yaml)
        uris = [u.uri for u in result.uris]
        assert "/v1/orders" in uris
        assert "/v2/products/{sku}" in uris

    def test_role_is_declaration(self, sample_json):
        parser = JsonYamlParser()
        result = parser.parse(sample_json)
        for u in result.uris:
            assert u.role == "declaration"

    def test_invalid_json(self, tmp_dir):
        p = tmp_dir / "bad.json"
        p.write_text("not valid json{{{", encoding="utf-8")
        parser = JsonYamlParser()
        result = parser.parse(p)
        assert result.uris == []

    def test_empty_yaml(self, tmp_dir):
        p = tmp_dir / "empty.yaml"
        p.write_text("", encoding="utf-8")
        parser = JsonYamlParser()
        result = parser.parse(p)
        assert result.uris == []

    def test_unsupported_extension(self, tmp_dir):
        p = tmp_dir / "data.csv"
        p.write_text("/api/v1/test", encoding="utf-8")
        parser = JsonYamlParser()
        assert not parser.can_parse(p)
