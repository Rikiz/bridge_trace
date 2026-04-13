from __future__ import annotations

from unittest.mock import MagicMock

from bridgetrace.analysis.trace_engine import TraceEngine, TraceResult


class TestTraceResult:
    def test_empty_result(self):
        result = TraceResult([])
        assert result.to_dict_list() == []
        assert result.format_text() == "No trace results found."

    def test_with_records(self):
        records = [{"caller": "func_a", "callee": "func_b"}]
        result = TraceResult(records)
        assert result.to_dict_list() == records
        assert "func_a" in result.format_text()


class TestTraceEngine:
    def _make_engine(self, records=None):
        mock_client = MagicMock()
        mock_client.run.return_value = records or []
        return TraceEngine(mock_client), mock_client

    def test_trace_uri_with_group(self):
        engine, mock_client = self._make_engine([{"caller_name": "a"}])
        result = engine.trace_uri("/api/v1/users", group="myservice")
        assert len(result.records) == 1
        mock_client.run.assert_called_once()
        call_args = mock_client.run.call_args
        assert "group" in str(call_args)

    def test_trace_uri_fallback(self):
        engine, mock_client = self._make_engine()
        mock_client.run.side_effect = [
            [],
            [{"endpoint": "/api/v1/users"}],
        ]
        result = engine.trace_uri("/api/v1/users")
        assert len(result.records) == 1
        assert mock_client.run.call_count == 2

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
