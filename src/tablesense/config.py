"""Load and validate a per-venue YAML config (see configs/example_venue.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .state import Timers

Point = tuple[float, float]
DEFAULT_AREA = "main"


@dataclass(frozen=True)
class AreaConfig:
    """A part of the venue staff think of separately: a floor, a room, a terrace."""

    id: str
    name: str
    # Reachable without stairs (wheelchair, buggy). The host's wait estimate
    # can be limited to these areas.
    step_free: bool = True


@dataclass(frozen=True)
class CameraConfig:
    id: str
    source: str  # file path or rtsp:// URL
    fps: float = 3.0  # frames processed per second
    # Detector input size. 640 is fine for near cameras; high, wide or
    # straight-down cameras need 1280 (eval/results/2026-11-09_detector_stock_scenes.md).
    imgsz: int = 640
    conf: float = 0.4  # detection confidence threshold
    tiles: int = 1  # >1: detect on an n x n grid of tiles too (far/wide cameras; no tracking)
    area: str | None = None  # the area this camera looks at; its tables inherit it


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
    # Resolved when the config is parsed: the table's own `area`, else its
    # camera's, else the only area. Always set on a parsed config.
    area: str = DEFAULT_AREA


@dataclass(frozen=True)
class VenueConfig:
    venue_id: str
    timezone: str
    cameras: tuple[CameraConfig, ...]
    tables: tuple[TableConfig, ...]
    timers: Timers
    tabletop: dict[str, float] = field(default_factory=dict)  # TabletopParams overrides
    areas: tuple[AreaConfig, ...] = (AreaConfig(DEFAULT_AREA, "Main floor"),)

    def camera(self, camera_id: str) -> CameraConfig:
        for c in self.cameras:
            if c.id == camera_id:
                return c
        raise KeyError(f"no camera {camera_id!r}; have {[c.id for c in self.cameras]}")

    def tables_for(self, camera_id: str) -> tuple[TableConfig, ...]:
        return tuple(t for t in self.tables if t.camera == camera_id)

    def tables_in(self, area_id: str) -> tuple[TableConfig, ...]:
        return tuple(t for t in self.tables if t.area == area_id)


def _parse_areas(raw: Any) -> tuple[AreaConfig, ...]:
    if not raw:
        return (AreaConfig(DEFAULT_AREA, "Main floor"),)
    out = []
    for a in raw:
        if isinstance(a, str):
            a = {"id": a}
        aid = str(a["id"])
        out.append(AreaConfig(aid, str(a.get("name") or aid.replace("_", " ").capitalize()),
                              bool(a.get("step_free", True))))
    return tuple(out)


def parse_venue_config(raw: dict[str, Any]) -> VenueConfig:
    try:
        cameras = tuple(
            CameraConfig(
                id=str(c["id"]),
                source=str(c["source"]),
                fps=float(c.get("fps", 3)),
                imgsz=int(c.get("imgsz", 640)),
                conf=float(c.get("conf", 0.4)),
                tiles=int(c.get("tiles", 1)),
                area=str(c["area"]) if c.get("area") else None,
            )
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
                area=str(t["area"]) if t.get("area") else "",  # resolved below
            )
            for t in raw["tables"]
        )
        areas = _parse_areas(raw.get("areas"))
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
        if c.imgsz < 32 or c.imgsz % 32:
            raise ValueError(f"camera {c.id}: imgsz must be a multiple of 32")
        if not 1 <= c.tiles <= 4:
            raise ValueError(f"camera {c.id}: tiles must be 1-4")
        if not 0 < c.conf < 1:
            raise ValueError(f"camera {c.id}: conf must be between 0 and 1")
    area_ids = [a.id for a in areas]
    if len(set(area_ids)) != len(area_ids):
        raise ValueError("duplicate area ids")
    for c in cameras:
        if c.area is not None and c.area not in area_ids:
            raise ValueError(f"camera {c.id}: unknown area {c.area!r}; have {area_ids}")
    cam_area = {c.id: c.area for c in cameras}
    resolved = []
    for t in tables:
        area = t.area or cam_area.get(t.camera) or (area_ids[0] if len(area_ids) == 1 else "")
        if not area:
            raise ValueError(f"table {t.id}: which area? Set `area` on the table or on camera {t.camera!r}")
        if area not in area_ids:
            raise ValueError(f"table {t.id}: unknown area {area!r}; have {area_ids}")
        resolved.append(replace(t, area=area))
    tables = tuple(resolved)
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
        areas=areas,
    )


def load_venue_config(path: str | Path) -> VenueConfig:
    import yaml

    with open(path) as f:
        return parse_venue_config(yaml.safe_load(f))