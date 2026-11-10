"""Tiled detection: grid geometry and merging duplicates (no model needed)."""

import pytest

from tablesense.config import parse_venue_config
from tablesense.detect.people import Person, merge_people, tile_grid


def test_single_tile_is_the_frame():
    assert tile_grid(1280, 720, 1) == [(0, 0, 1280, 720)]


def test_grid_covers_frame_with_overlap():
    tiles = tile_grid(1280, 720, 2, overlap=0.2)
    assert len(tiles) == 4
    xs = sorted({(t[0], t[2]) for t in tiles})
    assert xs[0][0] == 0 and xs[-1][1] == 1280
    assert xs[0][1] > xs[1][0]  # they overlap
    overlap_px = xs[0][1] - xs[1][0]
    assert overlap_px == pytest.approx(0.2 * (xs[0][1] - xs[0][0]), abs=2)
    for n in (3, 4):
        t = tile_grid(1280, 720, n)
        assert len(t) == n * n and max(x[2] for x in t) == 1280 and max(x[3] for x in t) == 720


def test_bad_grid_args():
    with pytest.raises(ValueError):
        tile_grid(100, 100, 0)
    with pytest.raises(ValueError):
        tile_grid(100, 100, 2, overlap=1)


def P(x1, y1, x2, y2, c=0.9):
    return Person((x1, y1, x2, y2), c)


def test_same_person_from_two_passes_kept_once():
    out = merge_people([P(100, 100, 150, 220, 0.8), P(102, 98, 151, 222, 0.6)])
    assert len(out) == 1


def test_person_cut_at_tile_edge_merged_into_whole_box():
    whole = P(100, 100, 150, 220)
    cut = P(100, 100, 150, 160)  # top half, from a tile ending at y=160
    assert merge_people([cut, whole]) == [whole]


def test_neighbours_side_by_side_both_kept():
    a, b = P(100, 100, 150, 220), P(140, 100, 190, 220)  # small overlap
    assert len(merge_people([a, b])) == 2


def test_tiles_in_config():
    raw = {
        "venue_id": "v",
        "cameras": [{"id": "cam", "source": "x.mp4", "tiles": 2}],
        "tables": [{"id": "T1", "camera": "cam", "polygon": [[0, 0], [1, 0], [1, 1]]}],
    }
    assert parse_venue_config(raw).cameras[0].tiles == 2
    raw["cameras"][0]["tiles"] = 9
    with pytest.raises(ValueError, match="tiles"):
        parse_venue_config(raw)
