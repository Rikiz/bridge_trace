from __future__ import annotations

from typer.testing import CliRunner

from bridgetrace.cli.app import app

runner = CliRunner()


class TestBootstrapCommand:
    def test_bootstrap_help(self):
        result = runner.invoke(app, ["bootstrap", "--help"])
        assert result.exit_code == 0
        assert "Neo4j schema" in result.output


class TestGroupCommands:
    def test_group_list_empty(self):
        result = runner.invoke(app, ["group", "list"])
        assert result.exit_code == 0

    def test_group_add_and_list(self):
        result = runner.invoke(app, ["group", "add", "testgrp", "/tmp/fake"])
        assert result.exit_code == 0
        assert "saved" in result.output

        result = runner.invoke(app, ["group", "list"])
        assert result.exit_code == 0
        assert "testgrp" in result.output

        result = runner.invoke(app, ["group", "remove", "testgrp"])
        assert result.exit_code == 0
        assert "removed" in result.output


class TestScanCommand:
    def test_scan_missing_group(self):
        result = runner.invoke(app, ["scan", "nonexistent_group_xyz"])
        assert result.exit_code == 1

    def test_scan_help(self):
        result = runner.invoke(app, ["scan", "--help"])
        assert result.exit_code == 0
        assert "--clean" in result.output
        assert "--bootstrap" in result.output


class TestTraceCommands:
    def test_trace_help(self):
        result = runner.invoke(app, ["trace", "--help"])
        assert result.exit_code == 0
        assert "--cross-repo" in result.output
        assert "--impl" in result.output

    def test_trace_endpoint_calls_help(self):
        result = runner.invoke(app, ["trace-endpoint-calls", "--help"])
        assert result.exit_code == 0

    def test_trace_consumers_help(self):
        result = runner.invoke(app, ["trace-consumers", "--help"])
        assert result.exit_code == 0
