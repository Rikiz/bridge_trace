from __future__ import annotations

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
        assert "/v1/users/{id}" in impl_uris

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
