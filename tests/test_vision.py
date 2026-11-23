"""Cameras and clip checks: config editing, uploads, results and who may use them.

The video work itself runs in child processes with OpenCV and YOLO. Here the
frame grab and the analysis are replaced with stand-ins, so these tests run
without either; the grab command gets a real (OpenCV) test of its own.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

from tablesense.dashboard.auth import Auth
from tablesense.dashboard.server import Clock, Dashboard, make_handler
from tablesense.dashboard.vision import (
    CHECK_RETENTION_S,
    Proc,
    VenueFile,
    VisionManager,
    mask_source,
    parse_tables,
    summarize,
)
from tablesense.events.log import EventLog
from tablesense.live import AlertRules
from tablesense.state import State, Transition

PIN, PW = "4821", "owner-password-1"
SRC = "rtsp://admin:s3cret-pass@192.168.1.20:554/stream1"
SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]


def table(tid, x=0, seats=4):
    return {"id": tid, "seats": seats, "polygon": [[x, 0], [x + 100, 0], [x + 100, 100], [x, 100]]}


# --- validation -------------------------------------------------------------


def test_parse_tables_checks_and_clamps():
    out = parse_tables([{"id": "T1", "seats": "2", "polygon": [[-5, 0], [2000, 0], [10, 900.4]]}], 1280, 720)
    assert out == [{"id": "T1", "seats": 2, "polygon": [[0, 0], [1280, 0], [10, 720]]}]
    for bad, msg in [
        ([], "at least one"),
        ([{"id": "T 1", "polygon": SQUARE}], "table name"),
        ([table("T1"), table("T1")], "twice"),
        ([{"id": "T1", "polygon": SQUARE[:2]}], "3 to 40"),
        ([{"id": "T1", "seats": 0, "polygon": SQUARE}], "seats"),
        ([{"id": "T1", "polygon": [["a", 1], [1, 1], [1, 2]]}], "number"),
    ]:
        with pytest.raises(ValueError, match=msg):
            parse_tables(bad)


def test_mask_source_never_shows_the_password():
    assert mask_source(SRC) == "rtsp://192.168.1.20:554/stream1  (with login)"
    assert "s3cret" not in mask_source(SRC)
    assert mask_source("rtsp://10.0.0.5/live") == "rtsp://10.0.0.5/live"


def test_venue_file_validates_backs_up_and_is_private(tmp_path):
    vf = VenueFile(tmp_path / "venue.yaml", "my_cafe", "Europe/Dublin")
    raw = vf.load_raw()
    assert raw["venue_id"] == "my_cafe" and raw["cameras"] == []
    raw["cameras"].append({"id": "c1", "source": SRC})
    raw["tables"].append({"camera": "c1", **table("T1")})
    venue = vf.save_raw(raw)
    assert [t.id for t in venue.tables] == ["T1"]
    assert oct(vf.path.stat().st_mode & 0o777) == "0o600"
    vf.save_raw(raw)
    assert (tmp_path / "venue.yaml.bak").exists()
    raw["tables"][0]["area"] = "terrace"  # no such area
    with pytest.raises(ValueError, match="unknown area"):
        vf.save_raw(raw)
    assert vf.load_raw()["tables"][0].get("area") is None  # the bad save didn't land


# --- cameras ----------------------------------------------------------------


@pytest.fixture
def mgr(tmp_path):
    seen = []
    m = VisionManager(tmp_path / "data", str(tmp_path / "data" / "events.db"), "yolo11s.pt",
                      venue_file=VenueFile(tmp_path / "venue.yaml", "my_cafe", "Europe/Dublin"),
                      on_venue=seen.append)
    m.missing = []  # pretend OpenCV and YOLO are installed
    m.seen = seen
    m._restart = lambda cid: None  # don't start camera processes in tests

    def fake_grab(source, out, at, clip=False):
        Path(out).write_bytes(b"\xff\xd8 jpeg")
        return {"width": 1280, "height": 720, "duration": None if source.startswith("rtsp") else 600.0}

    m._grab = fake_grab
    return m


def test_add_camera_then_edit_without_retyping_the_password(mgr):
    mgr.save_camera({"id": "ground", "existing": False, "source": SRC, "fps": 3, "tables": [table("T1"), table("T2", 200)],
                     "width": 1280, "height": 720})
    cams = mgr.cameras()
    assert [c["id"] for c in cams] == ["ground"]
    assert "s3cret" not in json.dumps(cams)
    assert cams[0]["state"] == "off" and len(cams[0]["tables"]) == 2
    assert [t.id for t in mgr.seen[-1].tables] == ["T1", "T2"]  # the dashboard was told

    # Edit: new outlines, blank address keeps the stored one, old tabletop settings survive.
    raw = mgr.venue_file.load_raw()
    raw["tables"][0]["reference"] = "data/refs/T1.png"
    raw["tables"][0]["top"] = SQUARE
    mgr.venue_file.save_raw(raw)
    mgr.save_camera({"id": "ground", "existing": True, "source": "", "fps": 2, "tables": [table("T1", seats=6)]})
    raw = mgr.venue_file.load_raw()
    assert raw["cameras"][0]["source"] == SRC and raw["cameras"][0]["fps"] == 2.0
    assert [(t["id"], t["seats"], t.get("reference")) for t in raw["tables"]] == [("T1", 6, "data/refs/T1.png")]


def test_camera_rules(mgr):
    with pytest.raises(ValueError, match="stream address"):
        mgr.save_camera({"id": "c", "existing": False, "tables": [table("T1")]})
    with pytest.raises(ValueError, match="rtsp://"):
        mgr.save_camera({"id": "c", "existing": False, "source": "file:///etc/passwd", "tables": [table("T1")]})
    mgr.save_camera({"id": "c", "existing": False, "source": SRC, "tables": [table("T1")]})
    with pytest.raises(ValueError, match="already a camera"):
        mgr.save_camera({"id": "c", "existing": False, "source": SRC, "tables": [table("T9")]})
    with pytest.raises(ValueError, match="twice|duplicate"):  # table ids are unique across cameras
        mgr.save_camera({"id": "d", "existing": False, "source": SRC, "tables": [table("T1")]})
    with pytest.raises(ValueError, match="rtsp://"):
        mgr.test_source("/etc/passwd")


def test_start_stop_and_remove(mgr):
    mgr.save_camera({"id": "c", "existing": False, "source": SRC, "tables": [table("T1")]})
    mgr.set_live("c", True)
    assert mgr.venue_file.load_raw()["cameras"][0]["live"] is True
    mgr.set_live("c", False)
    assert mgr.venue_file.load_raw()["cameras"][0]["live"] is False
    mgr.delete_camera("c")
    assert mgr.venue_file.load_raw()["cameras"] == [] and mgr.venue_file.load_raw()["tables"] == []
    with pytest.raises(KeyError):
        mgr.set_live("c", True)


def test_demo_clock_and_missing_opencv_block_live_cameras(mgr):
    mgr.save_camera({"id": "c", "existing": False, "source": SRC, "tables": [table("T1")]})
    mgr.allow_live = False
    with pytest.raises(ValueError, match="demo"):
        mgr.set_live("c", True)
    mgr.allow_live, mgr.missing = True, ["opencv-python"]
    with pytest.raises(ValueError, match="install opencv-python"):
        mgr.set_live("c", True)


def test_no_config_means_no_cameras(tmp_path):
    m = VisionManager(tmp_path, str(tmp_path / "e.db"), "m.pt")
    assert m.cameras() == []
    with pytest.raises(ValueError, match="--config"):
        m.save_camera({"id": "c", "source": SRC, "tables": [table("T1")]})


def test_test_frames_are_served_only_by_token(mgr):
    d = mgr.test_source(SRC)
    assert d["width"] == 1280 and d["frame"].startswith("t-")
    assert mgr.frame_path(d["frame"]).exists()
    assert mgr.frame_path("t-../../etc/passwd") is None
    assert mgr.frame_path("row_cam") is None


# --- clip checks -----------------------------------------------------------


class Body:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def read(self, n):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk


def upload(mgr, data=b"x" * 5000, name="lunch.mp4"):
    return mgr.upload(Body(data), len(data), name)


def test_upload_rules(mgr):
    with pytest.raises(ValueError, match="video file"):
        upload(mgr, name="notes.txt")
    with pytest.raises(ValueError, match="empty"):
        mgr.upload(Body(b""), 0, "a.mp4")
    mgr.max_upload = 100
    with pytest.raises(ValueError, match="the most"):
        upload(mgr)
    mgr.max_upload = 10_000
    with pytest.raises(ValueError, match="stopped part way"):
        mgr.upload(Body(b"short"), 5000, "a.mp4")
    assert list(mgr.checks_dir.iterdir()) == []  # nothing left behind by failed uploads
    meta = upload(mgr, name="../../evil/lunch.mp4")
    assert meta["name"] == "lunch.mp4" and meta["state"] == "uploaded" and meta["duration"] == 600.0
    assert (mgr.checks_dir / meta["id"] / "video.mp4").stat().st_size == 5000


def fake_analysis(mgr, transitions, video_s=600.0):
    """Stand in for `tablesense.cli`: write what it would write, then exit 0."""
    def spawn(key, args, log_path, source):
        d = mgr.checks_dir / key
        log = EventLog(d / "events.db")
        run = log.start_run("run1", venue_id=f"check_{key}", camera_id="clip", source="replay", started_at=946684800.0)
        log.write_many(run, transitions)
        log.close()
        (d / "status.json").write_text(json.dumps({"state": "done", "frames": 1800, "video_ts": video_s,
                                                   "proc_fps": 6.1, "max_people": 5}))
        (d / "kf").mkdir()
        (d / "kf" / "keyframes.json").write_text(json.dumps([{"i": 1, "ts": 0, "table": None}]))
        return Proc(key, subprocess.Popen([sys.executable, "-c", "pass"]), time.time(), open(log_path, "wb"))
    mgr._spawn = spawn


def wait_for(mgr, job, state):
    for _ in range(100):
        mgr.supervise()
        if mgr.meta(job)["state"] == state:
            return mgr.job(job)
        time.sleep(0.05)
    raise AssertionError(mgr.meta(job))


def test_analysis_results_and_video_deleted(mgr):
    meta = upload(mgr)
    with pytest.raises(ValueError, match="at least one"):
        mgr.analyze(meta["id"], {"tables": []})
    fake_analysis(mgr, [
        Transition("T1", State.EMPTY, State.SEATED, 60, 105, 2, False, "seated"),
        Transition("T1", State.SEATED, State.NEEDS_CLEARING, 300, 330, 0, False, "hold_expired"),
        Transition("T1", State.NEEDS_CLEARING, State.EMPTY, 360, 370, 0, False, "clearing_visit"),
    ])
    q = mgr.analyze(meta["id"], {"tables": [table("T1"), table("T2", 200)], "fps": 3})
    assert q["state"] == "queued"
    cfg = json.loads((mgr.checks_dir / meta["id"] / "config.json").read_text())
    assert [t["id"] for t in cfg["tables"]] == ["T1", "T2"] and cfg["cameras"][0]["fps"] == 3
    with pytest.raises(ValueError, match="already"):
        mgr.analyze(meta["id"], {"tables": [table("T1")]})

    done = wait_for(mgr, meta["id"], "done")
    r = done["results"]
    t1, t2 = r["tables"]
    assert t1["segments"] == [{"color": "green", "start": 0.0, "end": 60.0}, {"color": "red", "start": 60.0, "end": 300.0},
                              {"color": "yellow", "start": 300.0, "end": 360.0},
                              {"color": "green", "start": 360.0, "end": 600.0}]
    assert t1["time_in"] == {"green": 300.0, "red": 240.0, "yellow": 60.0}
    assert t1["visits"] == 1 and t1["median_stay_s"] == 240 and t1["changes"] == 3
    assert t2["segments"] == [{"color": "green", "start": 0.0, "end": 600.0}] and t2["visits"] == 0
    assert r["changes"] == 3 and r["max_people"] == 5 and r["t_seat"] == 45
    assert not done["video_kept"] and not list((mgr.checks_dir / meta["id"]).glob("video.*"))
    with pytest.raises(ValueError, match="deleted"):
        mgr.analyze(meta["id"], {"tables": [table("T1")]})


def test_keep_video_failure_stop_and_delete(mgr):
    meta = upload(mgr)

    def failing(key, args, log_path, source):
        Path(log_path).write_text("loading model\nerror: cannot open video source: <source>\n")
        return Proc(key, subprocess.Popen([sys.executable, "-c", "raise SystemExit(2)"]), time.time(), open(os.devnull, "wb"))

    mgr._spawn = failing
    mgr.analyze(meta["id"], {"tables": [table("T1")], "keep_video": True})
    failed = wait_for(mgr, meta["id"], "error")
    assert "cannot open video source" in failed["error"] and failed["video_kept"]

    mgr._spawn = lambda key, *a: Proc(key, subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]),
                                      time.time(), open(os.devnull, "wb"))  # noqa: SIM115
    mgr.analyze(meta["id"], {"tables": [table("T1")], "keep_video": True})
    wait_for(mgr, meta["id"], "running")
    mgr.stop_job(meta["id"])
    assert mgr.meta(meta["id"])["state"] == "stopped" and mgr.current is None
    mgr.delete_job(meta["id"])
    assert not (mgr.checks_dir / meta["id"]).exists()
    with pytest.raises(KeyError):
        mgr.job(meta["id"])


def test_old_checks_are_removed(mgr):
    meta = upload(mgr)
    m = mgr.meta(meta["id"])
    m["created"] -= CHECK_RETENTION_S + 60
    mgr._save_meta(m)
    mgr._last_cleanup = 0
    mgr.supervise()
    assert not (mgr.checks_dir / meta["id"]).exists()


def test_check_files_only_by_name(mgr):
    meta = upload(mgr)
    assert mgr.check_file(meta["id"], "frame.jpg").exists()
    assert mgr.check_file(meta["id"], "meta.json") is None
    assert mgr.check_file(meta["id"], "../../venue.yaml") is None
    with pytest.raises(KeyError):
        mgr.check_file("../../etc", "frame.jpg")


def test_summarize_clips_times_to_the_clip():
    log = EventLog(":memory:")
    run = log.start_run("r", venue_id="v", camera_id="c", source="replay", started_at=1000.0)
    log.write_many(run, [Transition("T1", State.EMPTY, State.SEATED, 5, 50, 1, False, "seated")])
    r = summarize(log.events(), 1000.0, ["T1"], {"T1": 2}, duration=30.0)
    assert r["tables"][0]["segments"] == [{"color": "green", "start": 0.0, "end": 5.0},
                                          {"color": "red", "start": 5.0, "end": 30.0}]
    assert r["tables"][0]["visits"] == 1 and r["tables"][0]["median_stay_s"] is None  # still seated at the end


# --- the grab command, with real OpenCV ----------------------------------------


def test_grab_command_reads_a_real_video(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    video = tmp_path / "clip.avi"
    w = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 240))
    for i in range(30):
        w.write(np.full((240, 320, 3), i * 8, np.uint8))
    w.release()
    out = tmp_path / "f.jpg"
    env = {**os.environ, "TABLESENSE_SOURCE": str(video), "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    r = subprocess.run([sys.executable, "-m", "tablesense.dashboard.vision", "grab", str(out), "--at", "1"],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    info = json.loads(r.stdout.strip().splitlines()[-1])
    assert (info["width"], info["height"]) == (320, 240) and info["duration"] == pytest.approx(3.0)
    assert out.read_bytes()[:2] == b"\xff\xd8"

    env["TABLESENSE_SOURCE"] = str(tmp_path / "missing.mp4")
    r = subprocess.run([sys.executable, "-m", "tablesense.dashboard.vision", "grab", str(out)],
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 2 and "missing.mp4" not in r.stdout


# --- over HTTP: owner only ----------------------------------------------------------


@pytest.fixture
def server(mgr):
    log = EventLog(":memory:")
    dash = Dashboard(log, "my_cafe", {}, "Europe/Dublin", Clock(), AlertRules())
    mgr.on_venue = dash.set_venue
    srv = HTTPServer(("127.0.0.1", 0), make_handler(dash, Auth(PIN, PW), mgr))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", dash
    srv.shutdown()
    log.close()


def call(base, path, body=None, cookie=None, headers=None, raw=None):
    h = {"X-TableSense": "1", **({"Cookie": cookie} if cookie else {}), **(headers or {})}
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    r = urllib.request.Request(base + path, data=data, headers=h, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(r) as resp:
            ctype = resp.headers["Content-Type"]
            payload = resp.read()
            return resp.status, json.loads(payload) if "json" in ctype else payload, resp.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), e.headers


def login(base, secret):
    return call(base, "/api/login", {"secret": secret})[2]["Set-Cookie"].split(";")[0]


def test_only_the_owner_reaches_cameras_and_checks(server):
    base, _ = server
    assert call(base, "/api/vision")[0] == 401
    staff = login(base, PIN)
    assert call(base, "/api/vision", cookie=staff)[0] == 403
    assert call(base, "/api/vision/test", {"source": SRC}, cookie=staff)[0] == 403
    assert call(base, "/api/vision/checks", raw=b"x", cookie=staff, headers={"X-Filename": "a.mp4"})[0] == 403
    owner = login(base, PW)
    code, body, _ = call(base, "/api/vision", cookie=owner)
    assert code == 200 and body["config"] and body["cameras"] == [] and body["missing"] == []


def test_camera_and_check_flow_over_http(server):
    base, dash = server
    owner = login(base, PW)
    code, test, _ = call(base, "/api/vision/test", {"source": SRC}, cookie=owner)
    assert code == 200
    code, _, hdr = call(base, f"/api/vision/frames/{test['frame']}.jpg", cookie=owner)
    assert code == 200 and hdr["Content-Type"] == "image/jpeg"
    code, _, _ = call(base, "/api/vision/cameras", {"id": "ground", "existing": False, "source": SRC,
                                                    "tables": [table("T1"), table("T2", 200)]}, cookie=owner)
    assert code == 200
    assert set(dash.seats) == {"T1", "T2"}  # the floor picks up new tables straight away
    body = call(base, "/api/vision", cookie=owner)[1]
    assert "s3cret" not in json.dumps(body)
    assert call(base, "/api/vision/cameras/nope/start", {}, cookie=owner)[0] == 404
    assert call(base, "/api/vision/cameras/ground/preview.jpg", cookie=owner)[0] == 204  # not live yet

    code, meta, _ = call(base, "/api/vision/checks", raw=b"v" * 2048, cookie=owner,
                         headers={"X-Filename": "dinner%20rush.mp4", "Content-Type": "application/octet-stream"})
    assert code == 200 and meta["name"] == "dinner rush.mp4"
    assert call(base, f"/api/vision/checks/{meta['id']}/frame.jpg", cookie=owner)[0] == 200
    assert call(base, f"/api/vision/checks/{meta['id']}", cookie=owner)[1]["state"] == "uploaded"
    code, err, _ = call(base, "/api/vision/checks", raw=b"hello", cookie=owner, headers={"X-Filename": "a.txt"})
    assert code == 400 and "video file" in err["error"]
    assert call(base, f"/api/vision/checks/{meta['id']}/delete", {}, cookie=owner)[0] == 200
    assert call(base, f"/api/vision/checks/{meta['id']}", cookie=owner)[0] == 404


def test_upload_needs_the_header_and_a_sane_size(server):
    base, _ = server
    owner = login(base, PW)
    r = urllib.request.Request(base + "/api/vision/checks", data=b"x", method="POST",
                               headers={"Cookie": owner, "X-Filename": "a.mp4"})  # no X-TableSense
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(r)
    assert e.value.code == 403
    code, _, _ = call(base, "/api/vision/checks", raw=b"x", cookie=owner,
                      headers={"X-Filename": "a.mp4", "Content-Length": str(3 * 1024**3)})
    assert code == 413
