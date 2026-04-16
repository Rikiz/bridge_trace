from __future__ import annotations

from bridgetrace.utils import is_path_like, normalize_path, sanitize_for_id


class TestNormalizePath:
    def test_unix_absolute(self):
        result = normalize_path("/home/user/project")
        assert result.startswith("/")
        assert "\\" not in result

    def test_windows_absolute(self):
        result = normalize_path("C:/Users/project")
        assert "C:/" in result
        assert "\\" not in result

    def test_backslash_conversion(self):
        result = normalize_path("C:\\Users\\project")
        assert result == "C:/Users/project"

    def test_path_object(self):
        from pathlib import Path

        result = normalize_path(Path("/tmp/test"))
        assert "/" in result

    def test_windows_long_path_on_non_windows(self):
        long_path = "C:/" + "/".join(["a"] * 60) + "/file.txt"
        result = normalize_path(long_path)
        if not hasattr(__import__("os"), "name") or __import__("os").name != "nt":
            assert "C:/" in result


class TestIsPathLike:
    def test_unix_path_with_extension(self):
        assert is_path_like("/home/user/file.py")

    def test_api_uri_with_params(self):
        assert not is_path_like("/v1/users/{id}")

    def test_api_uri_without_params(self):
        assert not is_path_like("/api/v1/users")

    def test_windows_path(self):
        assert is_path_like("C:\\Users\\file")

    def test_url_not_path(self):
        assert not is_path_like("https://example.com")

    def test_relative_path(self):
        assert is_path_like("./README.md")

    def test_plain_text(self):
        assert not is_path_like("hello")


class TestSanitizeForId:
    def test_path_sanitize(self):
        result = sanitize_for_id("/home/user/project")
        assert "\\" not in result

    def test_non_path_passthrough(self):
        result = sanitize_for_id("simple_string")
        assert result == "simple_string"
