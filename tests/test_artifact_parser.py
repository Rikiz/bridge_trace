from __future__ import annotations

from unittest.mock import MagicMock, patch

from bridgetrace.parsers.artifact_parser import ArtifactParser


class TestArtifactParser:
    def test_can_parse_class(self, tmp_dir):
        p = tmp_dir / "Test.class"
        p.write_bytes(b"\xca\xfe\xba\xbe")
        parser = ArtifactParser()
        assert parser.can_parse(p)

    def test_cannot_parse_java(self, tmp_dir):
        p = tmp_dir / "Test.java"
        p.write_text("class Test {}", encoding="utf-8")
        parser = ArtifactParser()
        assert not parser.can_parse(p)

    def test_extract_with_path_params(self, tmp_dir):
        p = tmp_dir / "Controller.class"
        p.write_bytes(b"\xca\xfe\xba\xbe")
        mock_output = (
            "RuntimeVisibleAnnotations:\n"
            '  Annotation: @GetMapping("/v1/users/{id}")\n'
            '  Annotation: @PostMapping("/v1/tenant-configs/${tenantId}")\n'
        )
        parser = ArtifactParser()
        with patch("bridgetrace.parsers.artifact_parser.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = mock_output
            mock_run.return_value = mock_result
            result = parser.parse(p)

        uris = [u.uri for u in result.uris]
        assert "/v1/users/{id}" in uris
        assert "/v1/tenant-configs/${tenantId}" in uris

    def test_extract_non_uri_quoted_string_skipped(self, tmp_dir):
        p = tmp_dir / "Test.class"
        p.write_bytes(b"\xca\xfe\xba\xbe")
        mock_output = 'RuntimeVisibleAnnotations:\n  Annotation: @SomeAnnotation("not-a-uri")\n'
        parser = ArtifactParser()
        with patch("bridgetrace.parsers.artifact_parser.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = mock_output
            mock_run.return_value = mock_result
            result = parser.parse(p)

        assert len(result.uris) == 0

    def test_javap_not_found(self, tmp_dir):
        p = tmp_dir / "Test.class"
        p.write_bytes(b"\xca\xfe\xba\xbe")
        parser = ArtifactParser()
        with patch("bridgetrace.parsers.artifact_parser.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = parser.parse(p)

        assert result.uris == []

    def test_javap_timeout(self, tmp_dir):
        import subprocess

        p = tmp_dir / "Test.class"
        p.write_bytes(b"\xca\xfe\xba\xbe")
        parser = ArtifactParser()
        with patch("bridgetrace.parsers.artifact_parser.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="javap", timeout=30)
            result = parser.parse(p)

        assert result.uris == []
