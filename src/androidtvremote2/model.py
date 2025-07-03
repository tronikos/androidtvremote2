"""Data models for AndroidTVRemote."""

from __future__ import annotations

from typing import TypedDict


class DeviceInfo(TypedDict):
    """A TypedDict for device information."""

    manufacturer: str
    model: str
    sw_version: str


class VolumeInfo(TypedDict):
    """A TypedDict for volume information."""

    level: int
    max: int
    muted: bool
