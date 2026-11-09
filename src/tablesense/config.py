"""Load and validate a per-venue YAML config (see configs/example_venue.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .state import Timers

Point = tuple[float, float]


@dataclass(frozen=True)
class CameraConfig:
    id: str
    source: str  # file path or rtsp:// URL
    fps: float = 3.0  # frames processed per second


@dataclass(frozen=True)
class TableConfig:
    id: str
    camera: str
    polygon: tuple[Point, ...]
    seats: int | None = None
    # Optional tabletop check (detect/tabletop.py): the table surface only,
    # and a grayscale image of it clean, from tools/capture_reference.py.
    top: tuple[Point, ...] | None = None
    reference: str | None = None


@dataclass(frozen=True)
class VenueConfig:
    venue_id: str
    timezone: str
    cameras: tuple[CameraConfig, ...]
    tables: tuple[TableConfig, ...]
    timers: Timers
    tabletop: dict[str, float] = field(default_factory=dict)  # TabletopParams overrides

    def camera(self, camera_id: str) -> CameraConfig:
        for c in self.cameras:
            if c.id == camera_id:
                return c
        raise KeyError(f"no camera {camera_id!r}; have {[c.id for c in self.cameras]}")

    def tables_for(self, camera_id: str) -> tuple[TableConfig, ...]:
        return tuple(t for t in self.tables if t.camera == camera_id)


def parse_venue_config(raw: dict[str, Any]) -> VenueConfig:
    try:
        cameras = tuple(
            CameraConfig(id=str(c["id"]), source=str(c["source"]), fps=float(c.get("fps", 3)))
            for c in raw["cameras"]
        )
        tables = tuple(
            TableConfig(
                id=str(t["id"]),
                camera=str(t["camera"]),
                polygon=tuple((float(x), float(y)) for x, y in t["polygon"]),
                seats=t.get("seats"),
                top=tuple((float(x), float(y)) for x, y in t["top"]) if t.get("top") else None,
                reference=str(t["reference"]) if t.get("reference") else None,
            )
            for t in raw["tables"]
        )
        venue_id = str(raw["venue_id"])
    except KeyError as e:
        raise ValueError(f"venue config missing key: {e}") from None

    camera_ids = [c.id for c in cameras]
    if len(set(camera_ids)) != len(camera_ids):
        raise ValueError("duplicate camera ids")
    table_ids = [t.id for t in tables]
    if len(set(table_ids)) != len(table_ids):
        raise ValueError("duplicate table ids")
    for c in cameras:
        if c.fps <= 0:
            raise ValueError(f"camera {c.id}: fps must be > 0")
    for t in tables:
        if t.camera not in camera_ids:
            raise ValueError(f"table {t.id}: unknown camera {t.camera!r}")
        if len(t.polygon) < 3:
            raise ValueError(f"table {t.id}: polygon needs at least 3 points")
        if t.top is not None and len(t.top) < 3:
            raise ValueError(f"table {t.id}: top needs at least 3 points")
        if t.reference and not t.top:
            raise ValueError(f"table {t.id}: reference given without a top polygon")

    return VenueConfig(
        venue_id=venue_id,
        timezone=str(raw.get("timezone", "UTC")),
        cameras=cameras,
        tables=tables,
        timers=Timers.from_dict(raw.get("timers", {})),
        tabletop={str(k): float(v) for k, v in (raw.get("tabletop") or {}).items()},
    )


def load_venue_config(path: str | Path) -> VenueConfig:
    import yaml

    with open(path) as f:
        return parse_venue_config(yaml.safe_load(f))
