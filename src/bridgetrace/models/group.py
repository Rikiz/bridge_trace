"""Group management models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Group(BaseModel):
    """A logical group that binds multiple repository paths together."""

    name: str
    paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    metadata_: dict[str, str] = Field(default_factory=dict, alias="metadata")
