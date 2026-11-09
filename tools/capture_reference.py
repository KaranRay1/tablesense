"""Capture a clean-tabletop reference image for the tabletop check.

Run it on a stretch of video where the table is clean, set and empty (at a
venue: before opening). It takes the per-pixel median over many frames, so
people walking past don't end up in the reference.

    python tools/capture_reference.py --config configs/my_venue.yaml --table T2 \\
        --from 0 --to 60 --out data/refs/my_venue/T2.png

Then add to that table in the config:  reference: data/refs/my_venue/T2.png
(it also needs a `top:` polygon: the table surface only, no chairs).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tablesense.config import load_venue_config
from tablesense.detect.tabletop import prepare
from tablesense.ingest.video import read_frames


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--source", help="override the camera source")
    p.add_argument("--from", dest="t_from", type=float, default=0.0, help="start, seconds into the video")
    p.add_argument("--to", dest="t_to", type=float, required=True, help="end, seconds into the video")
    p.add_argument("--fps", type=float, default=4.0, help="frames sampled per second")
    p.add_argument("--out", required=True, help="PNG to write")
    args = p.parse_args(argv)

    import cv2
    import numpy as np

    venue = load_venue_config(args.config)
    table = next((t for t in venue.tables if t.id == args.table), None)
    if table is None:
        p.error(f"no table {args.table!r} in {args.config}")
    if not table.top:
        p.error(f"table {args.table} has no `top:` polygon in {args.config}")
    source = args.source or venue.camera(table.camera).source

    crops = [
        prepare(f.image, table.top)
        for f in read_frames(source, sample_fps=args.fps, max_seconds=args.t_to)
        if f.ts >= args.t_from
    ]
    if len(crops) < 3:
        p.error(f"only {len(crops)} frames in {args.t_from}-{args.t_to}s; need at least 3")
    ref = np.median(np.stack(crops), axis=0)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(args.out, np.clip(ref, 0, 255).astype(np.uint8))
    print(f"wrote {args.out} (median of {len(crops)} frames)")
    print(f"config:  reference: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
