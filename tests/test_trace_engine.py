from __future__ import annotations

from unittest.mock import MagicMock

from bridgetrace.analysis.trace_engine import (
    TraceEngine,
    TraceResult,
    _extract_subpath_keys,
    _normalize_uri_params,
)


class TestNormalizeUriParams:
    def test_dollar_to_brace(self):
        assert _normalize_uri_params("/v1/users/${id}") == "/v1/users/{id}"

    def test_already_brace(self):
        assert _normalize_uri_params("/v1/users/{id}") == "/v1/users/{id}"

    def test_no_params(self):
        assert _normalize_uri_params("/v1/users") == "/v1/users"

    def test_mixed_params(self):
        result = _normalize_uri_params("/api/${userId}/items/{itemId}")
        assert "${" not in result


class TestTraceSubpathKeys:
    def test_basic(self):
        keys = _extract_subpath_keys("/v1/users/{id}")
        assert len(keys) >= 1
        assert any("v1" in k for k in keys)


class TestTraceResult:
    def test_empty_result(self):
        result = TraceResult([])
        assert result.to_dict_list() == []
        assert result.format_text() == "No trace results found."

    def test_with_records(self):
        result = TraceResult([{"caller": "func_a"}])
        assert "func_a" in result.format_text()

    def test_strategy_label(self):
        result = TraceResult([{"x": 1}], strategy="subpath_fuzzy")
        assert "subpath_fuzzy" in result.format_text()


class TestTraceEngine:
    def _make_engine(self, records=None):
        mock_client = MagicMock()
        mock_client.run.return_value = records or []
        return TraceEngine(mock_client), mock_client

    def test_trace_uri_exact_match(self):
        engine, mock_client = self._make_engine([{"caller_name": "a"}])
        result = engine.trace_uri("/api/v1/users", group="myservice")
        assert len(result.records) == 1
        assert result.strategy == "exact_match"

    def test_trace_uri_normalized_fallback(self):
        engine, mock_client = self._make_engine()
        mock_client.run.side_effect = [
            [],  # exact match fails
            [],  # cross_repo_full exact fails
            [{"endpoint": "/api/v1/users"}],  # normalized match succeeds
        ]
        result = engine.trace_uri("/api/v1/users/${id}")
        assert result.strategy == "normalized_match"

    def test_trace_uri_to_implementation(self):
        engine, mock_client = self._make_engine([{"impl_name": "getUser"}])
        result = engine.trace_uri_to_implementation("/api/v1/users", "myservice")
        assert result.records[0]["impl_name"] == "getUser"

    def test_trace_endpoint_calls(self):
        engine, mock_client = self._make_engine([{"called_endpoint": "/v1/orders"}])
        result = engine.trace_endpoint_calls("/api/v1/users")
        assert result.records[0]["called_endpoint"] == "/v1/orders"

    def test_trace_cross_repo(self):
        engine, mock_client = self._make_engine([{"target_endpoint": "/v1/users"}])
        result = engine.trace_cross_repo("/data/v1/users")
        assert result.records[0]["target_endpoint"] == "/v1/users"

    def test_trace_consumers(self):
        engine, mock_client = self._make_engine([{"consumer_function": "fetchData"}])
        result = engine.trace_consumers("/api/v1/users")
        assert result.records[0]["consumer_function"] == "fetchData"

    def test_trace_uri_no_results(self):
        engine, mock_client = self._make_engine()
        result = engine.trace_uri("/nonexistent")
        assert len(result.records) == 0
