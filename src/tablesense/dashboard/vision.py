"""Live cameras and clip checks for the web app (owner only).

The web app stays standard library + PyYAML. Video work runs in child
processes (`python -m tablesense.cli` per camera or clip, and this module's
`grab` command for a single frame), so a frozen or crashing camera can't take
the web app down, and the web app still runs (without these features) on a
box without OpenCV.

Files, all under `root` (the event log's folder, git-ignored):
    live/<camera>.json   status from the camera's process (fps, people, states)
    live/<camera>.jpg    annotated preview, written only while the owner watches
    live/<camera>.log    the process's output (camera address removed)
    checks/<id>/         one uploaded clip: meta.json, the video (deleted after
                         analysis unless kept), frame.jpg, events.db, kf/ stills
Camera addresses live only in the venue config (a git-ignored file, mode 600)
and reach the child process through an environment variable, never its
command line. The browser only ever sees them with the password removed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from ..config import VenueConfig, parse_venue_config
from ..events.log import EventLog, strip_credentials

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpg", ".mpeg", ".ts"}
MAX_UPLOAD = 2 * 1024**3  # bytes
SOURCE_ENV = "TABLESENSE_SOURCE"
GRAB_TIMEOUT_S = 25
CHECK_RETENTION_S = 7 * 24 * 3600
FPS_CHOICES = (1.0, 2.0, 3.0, 5.0)
COLOR = {"EMPTY": "green", "SEATED": "red", "AWAY": "red", "NEEDS_CLEARING": "yellow"}


def missing_modules() -> list[str]:
    """What this machine lacks for video work (empty = ready)."""
    need = {"cv2": "opencv-python", "ultralytics": "ultralytics"}
    return [pkg for mod, pkg in need.items() if importlib.util.find_spec(mod) is None]


def mask_source(source: str) -> str:
    """For the browser: never the password, but say there is a login."""
    if not source:
        return ""
    clean = strip_credentials(source)
    return f"{clean}  (with login)" if clean != source else clean


def _hide(text: str, secret: str) -> str:
    return text.replace(secret, "<camera address>") if secret else text


def _write_json(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _num(v: Any, lo: float, hi: float, what: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if not lo <= x <= hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return x


def parse_tables(raw: Any, width: int | None = None, height: int | None = None) -> list[dict]:
    """Validate table outlines drawn in the browser: [{id, seats, polygon, area?}]."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("draw at least one table")
    if len(raw) > 60:
        raise ValueError("at most 60 tables per camera")
    width = int(_num(width, 1, 1e5, "width")) if width else None
    height = int(_num(height, 1, 1e5, "height")) if height else None
    out, seen = [], set()
    for t in raw:
        if not isinstance(t, dict):
            raise ValueError("bad table")  # noqa: TRY004 (a 400 for the browser)
        tid = str(t.get("id", "")).strip()
        if not ID_RE.fullmatch(tid):
            raise ValueError(f"table name {tid!r}: use 1-32 letters, digits, - or _")
        if tid in seen:
            raise ValueError(f"table {tid} is drawn twice")
        seen.add(tid)
        poly = t.get("polygon")
        if not isinstance(poly, list) or not 3 <= len(poly) <= 40:
            raise ValueError(f"table {tid}: an outline needs 3 to 40 points")
        pts = []
        for p in poly:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise ValueError(f"table {tid}: bad point")
            x, y = _num(p[0], -1e5, 1e5, "x"), _num(p[1], -1e5, 1e5, "y")
            if width:
                x = min(max(x, 0), width)
            if height:
                y = min(max(y, 0), height)
            pts.append([round(x), round(y)])
        row = {"id": tid, "seats": int(_num(t.get("seats", 4), 1, 30, f"table {tid}: seats")), "polygon": pts}
        if t.get("area"):
            row["area"] = str(t["area"])
        out.append(row)
    return out


# --------------------------------------------------------------------------- venue file


class VenueFile:
    """The venue config on disk, edited from the web app.

    Each save keeps the previous file as <name>.bak (comments in a
    hand-written file are not kept by a save, the backup has them)."""

    def __init__(self, path: str | Path, venue_id: str, timezone: str) -> None:
        self.path = Path(path)
        self.default = {"venue_id": venue_id, "timezone": timezone, "cameras": [], "tables": []}

    def load_raw(self) -> dict:
        import yaml

        if not self.path.exists():
            return json.loads(json.dumps(self.default))
        with open(self.path) as f:
            raw = yaml.safe_load(f) or {}
        raw.setdefault("cameras", [])
        raw.setdefault("tables", [])
        return raw

    def save_raw(self, raw: dict) -> VenueConfig:
        import yaml

        venue = parse_venue_config(raw)  # refuse anything the pipeline would refuse
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".venue-")
        with os.fdopen(fd, "w") as f:
            f.write("# Edited by the TableSense web app. The previous version is in the .bak file next to this one.\n")
            yaml.safe_dump(raw, f, sort_keys=False, default_flow_style=None, width=120)
        os.chmod(tmp, 0o600)  # camera passwords
        os.replace(tmp, self.path)
        return venue


# --------------------------------------------------------------------------- processes


@dataclass
class Proc:
    key: str
    proc: subprocess.Popen
    started: float
    log: IO[bytes]


@dataclass
class CameraState:
    failures: int = 0
    next_try: float = 0.0
    last_exit: int | None = None
    last_error: str | None = None
    proc: Proc | None = None


@dataclass
class VisionManager:
    root: Path
    events_path: str
    model: str
    venue_file: VenueFile | None = None
    on_venue: Callable[[VenueConfig], None] | None = None
    allow_live: bool = True  # off with a demo clock: live events would land at the wrong time
    python: str = field(default=sys.executable)
    max_upload: int = MAX_UPLOAD

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.live_dir = self.root / "live"
        self.checks_dir = self.root / "checks"
        self.live_dir.mkdir(parents=True, exist_ok=True)
        self.checks_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cams: dict[str, CameraState] = {}
        self.current: Proc | None = None
        self.missing = missing_modules()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_cleanup = 0.0
        # A check that was running when the server stopped won't finish now.
        for meta in self._all_meta():
            if meta["state"] in ("running", "queued"):
                meta.update(state="stopped", error="The app restarted during analysis. Analyse it again.")
                self._save_meta(meta)

    # --- lifecycle ---------------------------------------------------------

    def start(self, interval: float = 2.0) -> None:
        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.supervise()
                except Exception as e:  # noqa: BLE001 (keep supervising whatever one camera does)
                    print(f"vision supervisor: {e!r}", flush=True)

        self._thread = threading.Thread(target=loop, name="vision", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        with self.lock:
            for st in self.cams.values():
                self._kill(st.proc)
                st.proc = None
            self._kill(self.current)
            self.current = None
        for p in self.live_dir.glob("*.jpg*"):
            p.unlink(missing_ok=True)

    @staticmethod
    def _kill(p: Proc | None) -> None:
        if p is None:
            return
        if p.proc.poll() is None:
            p.proc.terminate()
            try:
                p.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.proc.kill()
        p.log.close()

    def _spawn(self, key: str, args: list[str], log_path: Path, source: str) -> Proc:
        log = open(log_path, "wb")  # noqa: SIM115 (open for the life of the process)
        env = {**os.environ, SOURCE_ENV: source, "PYTHONUNBUFFERED": "1", "OPENCV_LOG_LEVEL": "ERROR"}
        proc = subprocess.Popen([self.python, "-m", *args], stdout=log, stderr=subprocess.STDOUT, env=env,
                                stdin=subprocess.DEVNULL)
        return Proc(key, proc, time.time(), log)

    def supervise(self) -> None:
        """Keep live cameras running and run queued clip checks, one at a time."""
        now = time.time()
        with self.lock:
            venue_raw = self.venue_file.load_raw() if self.venue_file else {"cameras": []}
            wanted = {str(c["id"]): str(c.get("source", "")) for c in venue_raw["cameras"]
                      if c.get("live") and self.allow_live and not self.missing}
            for cid in list(self.cams) + list(wanted):
                st = self.cams.setdefault(cid, CameraState())
                running = st.proc is not None and st.proc.proc.poll() is None
                if st.proc is not None and not running:  # it exited
                    ran = now - st.proc.started
                    st.last_exit = st.proc.proc.returncode
                    st.last_error = self._exit_reason(cid, wanted.get(cid, ""))
                    st.proc.log.close()
                    st.proc = None
                    st.failures = 1 if ran > 600 else st.failures + 1
                    st.next_try = now + min(300, 5 * 2 ** (st.failures - 1))
                if cid not in wanted:
                    if running:
                        self._kill(st.proc)
                        st.proc = None
                    st.failures, st.next_try = 0, 0.0
                    continue
                if st.proc is None and now >= st.next_try:
                    self._start_camera(cid, wanted[cid])

            if self.current is not None and self.current.proc.poll() is not None:
                self._finish_job(self.current)
                self.current = None
            if self.current is None and not self.missing:
                queued = sorted((m for m in self._all_meta() if m["state"] == "queued"), key=lambda m: m["queued_at"])
                if queued:
                    self._start_job(queued[0])

            if now - self._last_cleanup > 3600:
                self._last_cleanup = now
                for m in self._all_meta():
                    if now - m["created"] > CHECK_RETENTION_S and m["state"] != "running":
                        shutil.rmtree(self.checks_dir / m["id"], ignore_errors=True)

    def _exit_reason(self, cid: str, source: str) -> str:
        st = _read_json(self.live_dir / f"{cid}.json", {})
        if st.get("error") and "cannot open video source" in st["error"]:
            return "Can't connect to the camera. Check it's switched on and on the same network as this box."
        if st.get("error"):
            return str(st["error"])
        if st.get("state") == "lost":
            return "The camera stream stopped and didn't come back."
        try:
            tail = (self.live_dir / f"{cid}.log").read_text(errors="replace").strip().splitlines()[-3:]
        except OSError:
            tail = []
        return _hide(" · ".join(tail), source)[-300:] or "The camera process stopped."

    def _start_camera(self, cid: str, source: str) -> None:
        (self.live_dir / f"{cid}.json").unlink(missing_ok=True)
        args = ["tablesense.cli", "--config", str(self.venue_file.path), "--camera", cid,
                "--source-env", SOURCE_ENV, "--model", self.model, "--events", self.events_path,
                "--status-file", str(self.live_dir / f"{cid}.json"), "--preview", str(self.live_dir / f"{cid}.jpg")]
        self.cams[cid].proc = self._spawn(cid, args, self.live_dir / f"{cid}.log", source)

    # --- cameras -----------------------------------------------------------

    def _require_config(self) -> VenueFile:
        if self.venue_file is None:
            raise ValueError("Start the app with --config configs/your_venue.yaml to connect cameras.")
        return self.venue_file

    def cameras(self) -> list[dict]:
        if self.venue_file is None:
            return []
        raw = self.venue_file.load_raw()
        now = time.time()
        out = []
        with self.lock:
            for c in raw["cameras"]:
                cid = str(c["id"])
                st = self.cams.get(cid, CameraState())
                status = _read_json(self.live_dir / f"{cid}.json", {})
                running = st.proc is not None and st.proc.proc.poll() is None
                fresh = now - status.get("updated_at", 0) < 15
                if not c.get("live"):
                    state = "off"
                elif running and fresh and status.get("state") == "running":
                    state = "live"
                elif running:
                    state = "connecting"
                else:
                    state = "retrying"
                tables = [t for t in raw["tables"] if t.get("camera") == cid]
                out.append({
                    "id": cid, "source": mask_source(str(c.get("source", ""))), "area": c.get("area"),
                    "fps": c.get("fps", 3), "live": bool(c.get("live")), "state": state,
                    "retry_in_s": max(0, round(st.next_try - now)) if state == "retrying" else None,
                    "error": st.last_error if state in ("retrying", "connecting") else None,
                    "proc_fps": status.get("proc_fps") if state == "live" else None,
                    "people": status.get("people") if state == "live" else None,
                    "states": status.get("states", {}) if state == "live" else {},
                    "since": status.get("started_at") if state == "live" else None,
                    "tables": [{"id": t["id"], "seats": t.get("seats"), "area": t.get("area"),
                                "polygon": t.get("polygon")} for t in tables],
                })
        return out

    def test_source(self, source: str | None = None, camera: str | None = None) -> dict:
        """Grab one frame so the owner can see the camera works and draw tables on it."""
        if camera:
            src = next((str(c.get("source", "")) for c in self._require_config().load_raw()["cameras"]
                        if str(c["id"]) == camera), "")
            if not src:
                raise ValueError("no such camera")
        else:
            src = (source or "").strip()
        if not src:
            raise ValueError("enter the camera's stream address")
        if not re.match(r"^(rtsps?|https?)://", src):
            raise ValueError("use the camera's stream address, starting rtsp:// (or http://)")
        token = "t-" + uuid.uuid4().hex[:16]
        info = self._grab(src, self.live_dir / f"{token}.jpg", 0.0)
        for old in self.live_dir.glob("t-*.jpg"):  # test frames are for the next few minutes only
            if time.time() - old.stat().st_mtime > 900:
                old.unlink(missing_ok=True)
        return {"frame": token, **info}

    def _grab(self, source: str, out: Path, at: float, clip: bool = False) -> dict:
        if self.missing:
            raise ValueError("This box can't read video yet: install " + " and ".join(self.missing))
        try:
            r = subprocess.run([self.python, "-m", "tablesense.dashboard.vision", "grab", str(out), "--at", str(at)],
                               env={**os.environ, SOURCE_ENV: source, "OPENCV_LOG_LEVEL": "ERROR"}, capture_output=True, text=True, check=False,
                               timeout=GRAB_TIMEOUT_S, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ValueError("Reading the clip took too long." if clip else
                             "The camera didn't answer within 25 seconds. Check the address and that "
                             "this box is on the same network as the camera.") from None
        if r.returncode != 0:
            out = r.stdout.strip().splitlines()  # our own one-line reason; OpenCV's noise goes to stderr
            msg = _hide(out[-1] if out else "", source)
            if clip:
                raise ValueError(f"That file isn't a video TableSense can read ({msg or 'no picture'}). "
                                 "Try exporting it as MP4 (H.264).")
            raise ValueError(f"Couldn't read a picture from it ({msg or 'no answer'}). Check the address, "
                             "user name and password.")
        return json.loads(r.stdout.strip().splitlines()[-1])

    def frame_path(self, token: str) -> Path | None:
        if re.fullmatch(r"t-[0-9a-f]{16}", token):
            p = self.live_dir / f"{token}.jpg"
            return p if p.exists() else None
        return None

    def save_camera(self, body: dict) -> dict:
        vf = self._require_config()
        cid = str(body.get("id", "")).strip()
        if not ID_RE.fullmatch(cid):
            raise ValueError("camera name: use 1-32 letters, digits, - or _")
        tables = parse_tables(body.get("tables"), body.get("width"), body.get("height"))
        fps = _num(body.get("fps", 3), 0.5, 10, "frames per second")
        with self.lock:
            raw = vf.load_raw()
            cam = next((c for c in raw["cameras"] if str(c["id"]) == cid), None)
            source = str(body.get("source") or "").strip()
            if cam is None:
                if body.get("existing"):
                    raise ValueError("that camera was removed; reload the page")
                if not source:
                    raise ValueError("enter the camera's stream address")
                cam = {"id": cid}
                raw["cameras"].append(cam)
            elif body.get("existing") is False:
                raise ValueError(f"there is already a camera called {cid}")
            if source:
                if not re.match(r"^(rtsps?|https?)://", source):
                    raise ValueError("use the camera's stream address, starting rtsp:// (or http://)")
                cam["source"] = source
            cam["fps"] = fps
            if body.get("area"):
                cam["area"] = str(body["area"])
            else:
                cam.pop("area", None)
            cam["live"] = bool(body.get("live", cam.get("live", False)))
            old = {t["id"]: t for t in raw["tables"] if t.get("camera") == cid}
            raw["tables"] = [t for t in raw["tables"] if t.get("camera") != cid]
            for t in tables:
                keep = {k: v for k, v in old.get(t["id"], {}).items() if k in ("top", "reference")}
                raw["tables"].append({"id": t["id"], "camera": cid, "seats": t["seats"], "polygon": t["polygon"],
                                      **({"area": t["area"]} if t.get("area") else {}), **keep})
            venue = vf.save_raw(raw)
            self._restart(cid)
        if self.on_venue:
            self.on_venue(venue)
        return {"ok": True, "camera": cid}

    def set_live(self, cid: str, live: bool) -> None:
        vf = self._require_config()
        if live and not self.allow_live:
            raise ValueError("Live cameras are off in demo mode (the demo clock isn't real time).")
        if live and self.missing:
            raise ValueError("This box can't read video yet: install " + " and ".join(self.missing))
        with self.lock:
            raw = vf.load_raw()
            cam = next((c for c in raw["cameras"] if str(c["id"]) == cid), None)
            if cam is None:
                raise KeyError(cid)
            if not any(t.get("camera") == cid for t in raw["tables"]):
                raise ValueError("draw at least one table for this camera first")
            cam["live"] = live
            vf.save_raw(raw)
            self._restart(cid)

    def delete_camera(self, cid: str) -> None:
        vf = self._require_config()
        with self.lock:
            raw = vf.load_raw()
            if not any(str(c["id"]) == cid for c in raw["cameras"]):
                raise KeyError(cid)
            raw["cameras"] = [c for c in raw["cameras"] if str(c["id"]) != cid]
            raw["tables"] = [t for t in raw["tables"] if t.get("camera") != cid]
            venue = vf.save_raw(raw)
            self._restart(cid)
            for suffix in (".json", ".jpg", ".jpg.on", ".log"):
                (self.live_dir / f"{cid}{suffix}").unlink(missing_ok=True)
        if self.on_venue:
            self.on_venue(venue)

    def _restart(self, cid: str) -> None:
        st = self.cams.setdefault(cid, CameraState())
        self._kill(st.proc)
        st.proc, st.failures, st.next_try, st.last_error = None, 0, 0.0, None
        threading.Thread(target=self.supervise, daemon=True).start()

    def preview(self, cid: str) -> bytes | None:
        """The latest annotated frame. Asking for it is what keeps it coming:
        the camera process writes previews only while someone looks."""
        if not ID_RE.fullmatch(cid):
            return None
        flag = self.live_dir / f"{cid}.jpg.on"
        flag.touch()
        img = self.live_dir / f"{cid}.jpg"
        try:
            if time.time() - img.stat().st_mtime < 6:
                return img.read_bytes()
        except OSError:
            pass
        return None

    # --- clip checks -------------------------------------------------------

    def _meta_path(self, job: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", job):
            raise KeyError(job)
        return self.checks_dir / job / "meta.json"

    def _all_meta(self) -> list[dict]:
        out = []
        for p in self.checks_dir.glob("*/meta.json"):
            m = _read_json(p)
            if m:
                out.append(m)
        return sorted(out, key=lambda m: -m["created"])

    def _save_meta(self, meta: dict) -> None:
        _write_json(self.checks_dir / meta["id"] / "meta.json", meta)

    def meta(self, job: str) -> dict:
        m = _read_json(self._meta_path(job))
        if not m:
            raise KeyError(job)
        return m

    def upload(self, stream: IO[bytes], length: int, filename: str) -> dict:
        name = Path(filename or "clip.mp4").name[:120]
        ext = Path(name).suffix.lower()
        if ext not in VIDEO_EXT:
            raise ValueError(f"upload a video file ({', '.join(sorted(VIDEO_EXT))})")
        if length <= 0:
            raise ValueError("empty upload")
        if length > self.max_upload:
            raise ValueError(f"the most a clip can be is {self.max_upload // 1024**2} MB: cut a shorter section")
        if shutil.disk_usage(self.checks_dir).free < length + 1024**3:
            raise ValueError("not enough free disk space on this box for that clip")
        job = uuid.uuid4().hex[:12]
        d = self.checks_dir / job
        d.mkdir()
        video = d / f"video{ext}"
        try:
            left = length
            with open(video, "wb") as f:
                while left > 0:
                    chunk = stream.read(min(1 << 20, left))
                    if not chunk:
                        raise ValueError("the upload stopped part way; try again")
                    f.write(chunk)
                    left -= len(chunk)
            info = self._grab(str(video), d / "frame.jpg", 1.0, clip=True)
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        meta = {"id": job, "name": name, "size": length, "created": time.time(), "state": "uploaded",
                "width": info["width"], "height": info["height"], "duration": info.get("duration"),
                "video_kept": True, "keep_video": False, "fps": 3.0, "tables": [], "error": None}
        self._save_meta(meta)
        return meta

    def analyze(self, job: str, body: dict) -> dict:
        with self.lock:
            meta = self.meta(job)
            if meta["state"] in ("queued", "running"):
                raise ValueError("this clip is already being analysed")
            if not meta["video_kept"]:
                raise ValueError("this clip's video was deleted after its analysis; upload it again")
            if self.missing:
                raise ValueError("This box can't read video yet: install " + " and ".join(self.missing))
            tables = parse_tables(body.get("tables"), meta["width"], meta["height"])
            fps = _num(body.get("fps", 3), 0.5, 10, "frames per second")
            d = self.checks_dir / job
            for old in ("events.db", "status.json", "results.json", "run.log"):
                (d / old).unlink(missing_ok=True)
            shutil.rmtree(d / "kf", ignore_errors=True)
            video = next(d.glob("video.*"))
            timers = (self.venue_file.load_raw().get("timers") if self.venue_file else None) or {}
            cfg = {"venue_id": f"check_{job}", "timezone": "UTC",
                   "cameras": [{"id": "clip", "source": str(video), "fps": fps}],
                   "tables": [{"id": t["id"], "camera": "clip", "seats": t["seats"], "polygon": t["polygon"]}
                              for t in tables],
                   "timers": timers}
            parse_venue_config(cfg)
            _write_json(d / "config.json", cfg)  # YAML is a superset of JSON
            meta.update(state="queued", queued_at=time.time(), fps=fps, tables=tables, error=None,
                        keep_video=bool(body.get("keep_video")))
            self._save_meta(meta)
        threading.Thread(target=self.supervise, daemon=True).start()
        return meta

    def _start_job(self, meta: dict) -> None:
        d = self.checks_dir / meta["id"]
        video = next(d.glob("video.*"))
        args = ["tablesense.cli", "--config", str(d / "config.json"), "--source-env", SOURCE_ENV,
                "--model", self.model, "--events", str(d / "events.db"), "--status-file", str(d / "status.json"),
                "--keyframes-dir", str(d / "kf"), "--start", "2000-01-01T00:00:00+00:00"]
        self.current = self._spawn(meta["id"], args, d / "run.log", str(video))
        meta.update(state="running", started_at=time.time())
        self._save_meta(meta)

    def _finish_job(self, p: Proc) -> None:
        p.log.close()
        meta = self.meta(p.key)
        d = self.checks_dir / p.key
        status = _read_json(d / "status.json", {})
        if meta["state"] == "stopped":
            pass
        elif p.proc.returncode == 0 and status.get("state") == "done":
            meta["state"] = "done"
            _write_json(d / "results.json", self._results(meta))
            if not meta.get("keep_video"):
                for v in d.glob("video.*"):
                    v.unlink(missing_ok=True)
                meta["video_kept"] = False
        else:
            meta["state"] = "error"
            try:
                tail = (d / "run.log").read_text(errors="replace").strip().splitlines()[-2:]
            except OSError:
                tail = []
            meta["error"] = status.get("error") or " · ".join(tail)[-300:] or "analysis stopped"
        meta["finished_at"] = time.time()
        self._save_meta(meta)

    def stop_job(self, job: str) -> None:
        with self.lock:
            meta = self.meta(job)
            if meta["state"] == "queued":
                meta["state"] = "stopped"
                self._save_meta(meta)
            elif meta["state"] == "running" and self.current and self.current.key == job:
                meta["state"] = "stopped"
                self._save_meta(meta)
                self._kill(self.current)
                self.current = None

    def delete_job(self, job: str) -> None:
        with self.lock:
            self.meta(job)
            if self.current and self.current.key == job:
                self._kill(self.current)
                self.current = None
            shutil.rmtree(self.checks_dir / job, ignore_errors=True)

    def jobs(self) -> list[dict]:
        return [self._job_view(m, brief=True) for m in self._all_meta()]

    def job(self, job: str) -> dict:
        return self._job_view(self.meta(job), brief=False)

    def _job_view(self, m: dict, brief: bool) -> dict:
        d = self.checks_dir / m["id"]
        out = {k: m.get(k) for k in ("id", "name", "size", "created", "state", "width", "height", "duration",
                                     "video_kept", "keep_video", "fps", "error")}
        out["expires"] = m["created"] + CHECK_RETENTION_S
        if m["state"] in ("running", "queued"):
            st = _read_json(d / "status.json", {})
            dur = m.get("duration") or st.get("duration")
            out["progress"] = {"video_ts": st.get("video_ts", 0), "duration": dur, "proc_fps": st.get("proc_fps"),
                               "frames": st.get("frames", 0), "people": st.get("people"),
                               "pct": min(99, round(100 * st.get("video_ts", 0) / dur)) if dur else None}
            if m["state"] == "queued":
                out["queue_position"] = 1 + sum(1 for x in self._all_meta()
                                                if x["state"] == "queued" and x["queued_at"] < m["queued_at"])
        if not brief:
            out["tables"] = m.get("tables", [])
            if m["state"] == "done":
                out["results"] = _read_json(d / "results.json")
        return out

    def check_file(self, job: str, name: str) -> Path | None:
        d = self._meta_path(job).parent
        if name == "frame.jpg" or re.fullmatch(r"kf-\d{4}\.jpg", name):
            p = d / ("kf" if name.startswith("kf-") else "") / name
            return p if p.exists() else None
        return None

    def _results(self, meta: dict) -> dict:
        d = self.checks_dir / meta["id"]
        status = _read_json(d / "status.json", {})
        keyframes = _read_json(d / "kf" / "keyframes.json", [])
        log = EventLog(d / "events.db")
        try:
            run = log.run()
            events = log.events()
        finally:
            log.close()
        cfg = _read_json(d / "config.json", {})
        res = summarize(events, run.started_at, [t["id"] for t in meta["tables"]],
                         {t["id"]: t["seats"] for t in meta["tables"]},
                         duration=status.get("video_ts") or meta.get("duration") or 0.0,
                         status=status, keyframes=keyframes)
        res["t_seat"] = (cfg.get("timers") or {}).get("t_seat", 45)
        return res


def summarize(events, t0: float, table_ids: list[str], seats: dict[str, int], duration: float,
              status: dict | None = None, keyframes: list | None = None) -> dict:
    """What happened at each table during a clip, in seconds from its start."""
    from ..analytics.sessions import build_sessions

    by_table: dict[str, list] = {t: [] for t in table_ids}
    for e in events:
        by_table.setdefault(e.table_id, []).append(e)
    sessions = build_sessions(events)
    tables = []
    for tid in table_ids:
        evs = sorted(by_table[tid], key=lambda e: e.at)
        segs, t, color = [], 0.0, "green"
        for e in evs:
            at = min(max(e.at - t0, 0.0), duration)
            if at > t:
                segs.append({"color": color, "start": round(t, 1), "end": round(at, 1)})
            t, color = max(t, at), COLOR[e.to_state.value]
        if duration > t:
            segs.append({"color": color, "start": round(t, 1), "end": round(duration, 1)})
        merged: list[dict] = []
        for s in segs:
            if merged and merged[-1]["color"] == s["color"]:
                merged[-1]["end"] = s["end"]
            else:
                merged.append(s)
        time_in = {c: round(sum(s["end"] - s["start"] for s in merged if s["color"] == c), 1)
                   for c in ("green", "red", "yellow")}
        visits = [s for s in sessions if s.table_id == tid]
        stays = [s.left_at - s.seated_at for s in visits if s.left_at is not None]
        tables.append({
            "id": tid, "seats": seats.get(tid), "segments": merged, "time_in": time_in,
            "changes": len(evs), "visits": len(visits),
            "median_stay_s": round(statistics.median(stays)) if stays else None,
            "people_max": max((s.group_size for s in visits), default=0),
            "changes_list": [{"at": round(e.at - t0, 1), "seen_at": round(e.detected_at - t0, 1),
                              "from": e.from_state.value, "to": e.to_state.value, "reason": e.reason,
                              "people": e.people_count} for e in evs],
        })
    st = status or {}
    return {"duration": round(duration, 1), "frames": st.get("frames"), "proc_fps": st.get("proc_fps"),
            "max_people": st.get("max_people"), "changes": sum(t["changes"] for t in tables),
            "visits": sum(t["visits"] for t in tables), "tables": tables, "keyframes": keyframes or []}


# --------------------------------------------------------------------------- grab command


def _grab_main(argv: list[str]) -> int:
    """`python -m tablesense.dashboard.vision grab OUT.jpg [--at S]`: one frame
    from $TABLESENSE_SOURCE, printed as {"width", "height", "duration"}."""
    import argparse

    p = argparse.ArgumentParser(prog="tablesense.dashboard.vision grab")
    p.add_argument("out")
    p.add_argument("--at", type=float, default=0.0)
    a = p.parse_args(argv)
    import cv2

    from ..ingest.video import grab_frame, is_live, probe

    source = os.environ.get(SOURCE_ENV, "")
    try:
        img = grab_frame(source, a.at)
        duration = None if is_live(source) else probe(source).duration
    except OSError as e:
        print(str(e).replace(source, "<camera address>"))
        return 2
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        print("could not encode the frame")
        return 2
    Path(a.out).write_bytes(buf.tobytes())
    h, w = img.shape[:2]
    print(json.dumps({"width": int(w), "height": int(h), "duration": duration}))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "grab":
        raise SystemExit(_grab_main(sys.argv[2:]))
    raise SystemExit("usage: python -m tablesense.dashboard.vision grab OUT.jpg [--at SECONDS]")
