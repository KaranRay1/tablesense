"""Label table states while watching a video. Writes the CSV that
`python -m tablesense.evaluation` reads.

Usage:
    .venv/bin/python tools/label_video.py data/raw/own/clip.mp4 \\
        --config configs/my_venue.yaml --out eval/labels/clip.csv

Keys:
    space        play / pause
    , .          back / forward 1 s        [ ]   back / forward 10 s
    < >          playback speed down / up (0.5x .. 16x)
    1-9          select table (in config order)
    g r y        selected table became green / red / yellow at this moment
    u            undo last mark
    s            save                        q / Esc   save and quit

Tips:
- Mark the moment a change *really* happens: guests sit down, the last guest
  leaves, the table is cleared. Guests briefly away still count as red.
- Before your first mark for a table, its time is unlabeled (ignored).
  Start marking after ~1 minute so the system has warmed up.
- Marks are saved next to the CSV as <out>.marks.json, so you can resume.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2
import numpy as np

from tablesense.config import load_venue_config
from tablesense.evaluation import segments_from_marks
from tablesense.state import Color

BGR = {Color.GREEN: (80, 200, 60), Color.RED: (50, 50, 230), Color.YELLOW: (0, 210, 240)}
KEY_COLOR = {ord("g"): Color.GREEN, ord("r"): Color.RED, ord("y"): Color.YELLOW}
SPEEDS = [0.5, 1, 2, 4, 8, 16]


def color_at(marks: list[tuple[str, float, Color]], table_id: str, t: float) -> Color | None:
    current = None
    for tid, mt, c in sorted(marks, key=lambda m: m[1]):
        if tid == table_id and mt <= t:
            current = c
    return current


def save(marks, out: Path, duration: float) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{out}.marks.json").write_text(
        json.dumps([{"table_id": tid, "t": round(t, 2), "state": c.value} for tid, t, c in marks], indent=1)
    )
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table_id", "start", "end", "state"])
        for s in segments_from_marks(marks, end=duration):
            w.writerow([s.table_id, round(s.start, 2), round(s.end, 2), s.color.value])
    print(f"saved {len(marks)} marks -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--config", required=True)
    ap.add_argument("--camera", help="camera id (default: first)")
    ap.add_argument("--out", required=True, help="labels CSV to write")
    args = ap.parse_args()

    venue = load_venue_config(args.config)
    cam = venue.camera(args.camera) if args.camera else venue.cameras[0]
    tables = venue.tables_for(cam.id)
    out = Path(args.out)

    marks: list[tuple[str, float, Color]] = []
    marks_file = Path(f"{out}.marks.json")
    if marks_file.exists():
        marks = [(m["table_id"], float(m["t"]), Color(m["state"])) for m in json.loads(marks_file.read_text())]
        print(f"resumed {len(marks)} marks from {marks_file}")

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        sys.exit(f"cannot open {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = n_frames / fps

    pos, playing, speed_i, selected = 0, False, 1, 0
    win = "label: space play, 1-9 table, g/r/y mark, u undo, s save, q quit"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    frame = None
    last_pos = -1

    while True:
        if pos != last_pos:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, frame = cap.read()
            if not ok:
                pos, playing = max(0, n_frames - 1), False
                continue
            last_pos = pos
        t = pos / fps
        img = frame.copy()
        overlay = img.copy()
        for i, tb in enumerate(tables):
            c = color_at(marks, tb.id, t)
            pts = np.array(tb.polygon, np.int32)
            if c is not None:
                cv2.fillPoly(overlay, [pts], BGR[c])
            thick = 4 if i == selected else 1
            cv2.polylines(img, [pts], True, (255, 255, 255), thick)
            x, y = pts.min(axis=0)
            label = f"{i + 1}:{tb.id} {c.value if c else '-'}"
            cv2.putText(img, label, (int(x), int(y) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        img = cv2.addWeighted(overlay, 0.3, img, 0.7, 0)
        status = (
            f"{t:8.1f}s / {duration:.0f}s  {'PLAY' if playing else 'PAUSE'} {SPEEDS[speed_i]}x  "
            f"table={tables[selected].id}  marks={len(marks)}"
        )
        cv2.rectangle(img, (0, 0), (img.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(img, status, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        cv2.imshow(win, img)

        # Speeds >= 1 skip frames at normal delay; speeds < 1 lengthen the delay.
        delay = int(1000 / fps / min(1.0, SPEEDS[speed_i]))
        key = cv2.waitKey(max(1, delay) if playing else 50) & 0xFF
        step = {ord(","): -1, ord("."): 1, ord("["): -10, ord("]"): 10}
        if key in (ord("q"), 27):
            break
        elif key == ord(" "):
            playing = not playing
        elif key in step:
            pos = int(min(max(0, pos + step[key] * fps), n_frames - 1))
        elif key == ord("<"):
            speed_i = max(0, speed_i - 1)
        elif key == ord(">"):
            speed_i = min(len(SPEEDS) - 1, speed_i + 1)
        elif ord("1") <= key <= ord("9") and key - ord("1") < len(tables):
            selected = key - ord("1")
        elif key in KEY_COLOR:
            marks.append((tables[selected].id, round(t, 2), KEY_COLOR[key]))
            print(f"{tables[selected].id} -> {KEY_COLOR[key].value} at {t:.1f}s")
        elif key == ord("u") and marks:
            print("undo", marks.pop())
        elif key == ord("s"):
            save(marks, out, duration)
        if playing:
            pos = min(pos + max(1, round(SPEEDS[speed_i])), n_frames - 1)
            if pos >= n_frames - 1:
                playing = False

    save(marks, out, duration)
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
