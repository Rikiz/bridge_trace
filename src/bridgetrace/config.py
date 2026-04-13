from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    log_level: str = "INFO"

    ignore_gitignore: bool = True

    sqlite_path: str = str(Path.home() / ".bridgetrace" / "groups.db")

    scan_batch_size: int = 500

    @property
    def neo4j_auth(self) -> tuple[str, str]:
        """Return (user, password) tuple for Neo4j driver."""
        return (self.neo4j_user, self.neo4j_password)


settings = Settings()
