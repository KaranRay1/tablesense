"""Draw table polygons on a camera frame and print them as config YAML.

Usage:
    .venv/bin/python tools/draw_zones.py data/raw/own/clip.mp4 --camera cam_floor_1 [--at 10]

    left click   add a point to the current table
    right click  undo last point
    n            finish current table, start the next
    s            save: print YAML for the tables and write <video>.zones.png
    q / Esc      quit

Paste the printed YAML under `tables:` in your venue config.
"""

from __future__ import annotations

import argparse
import sys

import cv2
import numpy as np


def grab_frame(path: str, at_seconds: float):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"cannot open {path}")
    cap.set(cv2.CAP_PROP_POS_MSEC, at_seconds * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        sys.exit(f"cannot read a frame at {at_seconds}s from {path}")
    return frame


def to_yaml(tables: list[list[tuple[int, int]]], camera: str) -> str:
    lines = []
    for i, pts in enumerate(tables, start=1):
        poly = ", ".join(f"[{x}, {y}]" for x, y in pts)
        lines += [f"  - id: T{i}", f"    camera: {camera}", f"    polygon: [{poly}]"]
    return "\n".join(lines)


def render(base, tables, current):
    img = base.copy()
    for i, pts in enumerate(tables, start=1):
        arr = np.array(pts, np.int32)
        cv2.polylines(img, [arr], True, (80, 200, 60), 2)
        cv2.putText(img, f"T{i}", tuple(arr.min(axis=0)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 200, 60), 2)
    for p in current:
        cv2.circle(img, p, 4, (0, 210, 240), -1)
    if len(current) > 1:
        cv2.polylines(img, [np.array(current, np.int32)], False, (0, 210, 240), 1)
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--camera", default="cam_floor_1")
    ap.add_argument("--at", type=float, default=0.0, help="seconds into the video")
    args = ap.parse_args()

    base = grab_frame(args.video, args.at)
    tables: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            current.append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN and current:
            current.pop()

    win = "draw zones: click points, n=next table, s=save, q=quit"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        cv2.imshow(win, render(base, tables, current))
        key = cv2.waitKey(30) & 0xFF
        if key == ord("n"):
            if len(current) >= 3:
                tables.append(current.copy())
            current.clear()
        elif key == ord("s"):
            if len(current) >= 3:
                tables.append(current.copy())
                current.clear()
            print(to_yaml(tables, args.camera))
            out = f"{args.video}.zones.png"
            cv2.imwrite(out, render(base, tables, current))
            print(f"# preview saved to {out}", file=sys.stderr)
        elif key in (ord("q"), 27):
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
