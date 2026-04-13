"""Abstract base class for all BridgeTrace parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from bridgetrace.models.graph import ParseResult


class BaseParser(ABC):
    """Abstract parser interface. Every parser must implement ``parse``."""

    supported_extensions: tuple[str, ...] = ()

    def can_parse(self, path: Path) -> bool:
        """Check if this parser supports the given file extension."""
        return path.suffix in self.supported_extensions

    @abstractmethod
    def parse(self, path: Path) -> ParseResult:
        """Parse a single file and return structured results."""
        ...

    def parse_many(self, paths: list[Path]) -> list[ParseResult]:
        """Parse multiple files, skipping unsupported ones."""
        return [self.parse(p) for p in paths if self.can_parse(p)]
