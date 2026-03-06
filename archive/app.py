#!/usr/bin/env python3
"""USB webcam live stream server with HLS, idle pause, and viewer tracking."""

import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash

# ── Config ────────────────────────────────────────────────────────────────────
VIDEO_DEV  = "/dev/video0"
AUDIO_DEV  = "hw:1,0"       # set to "" to disable audio
WIDTH      = 1280
HEIGHT     = 720
FPS        = 30
PORT       = 8088

HLS_DIR       = Path("static/hls")
SNAPSHOT_DIR  = Path("static/snapshots")
TIMELAPSE_DIR = Path("static/timelapse")

SNAPSHOT_INTERVAL = 30   # seconds between snapshots
VIEWER_TTL        = 60   # seconds — clients heartbeat every 30s
IDLE_TIMEOUT      = 120  # seconds with no viewers before pausing stream

# ── Auth ──────────────────────────────────────────────────────────────────────
app  = Flask(__name__)
auth = HTTPBasicAuth()
USERS = {
    "admin": generate_password_hash("changeme"),
}

@auth.verify_password
def verify_password(username, password):
    if username in USERS and check_password_hash(USERS[username], password):
        return username


# ── Shared state ──────────────────────────────────────────────────────────────
active_viewers:  dict = {}
viewer_lock      = threading.Lock()
stream_generation = 0
stream_active    = threading.Event()
_stream_procs: dict  = {"cam": None, "audio": None}
_stream_lock     = threading.Lock()


# ── Capture thread ────────────────────────────────────────────────────────────
def capture_thread():
    global stream_generation
    HLS_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        stream_active.wait()
        print("Stream starting…")

        procs = []
        ffmpeg_args = [
            "ffmpeg", "-y",
            "-f", "v4l2", "-video_size", f"{WIDTH}x{HEIGHT}",
            "-framerate", str(FPS), "-i", VIDEO_DEV,
        ]

        if AUDIO_DEV:
            arecord = subprocess.Popen(
                ["arecord", "-D", AUDIO_DEV, "-f", "S16_LE", "-c1", "-r", "16000"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            procs.append(arecord)
            ffmpeg_args += ["-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "pipe:0"]
            ffmpeg_args += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                            "-c:a", "aac", "-b:a", "64k"]
            stdin = arecord.stdout
        else:
            arecord = None
            ffmpeg_args += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency"]
            stdin = None

        ffmpeg_args += [
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "5",
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", str(HLS_DIR / "seg%d.ts"),
            str(HLS_DIR / "stream.m3u8"),
        ]

        ffmpeg = subprocess.Popen(
            ffmpeg_args, stdin=stdin,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs.append(ffmpeg)
        if arecord:
            arecord.stdout.close()

        with _stream_lock:
            _stream_procs["cam"]   = ffmpeg
            _stream_procs["audio"] = arecord
        stream_generation += 1

        ffmpeg.wait()
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass

        with _stream_lock:
            _stream_procs["cam"] = _stream_procs["audio"] = None

        if stream_active.is_set():
            time.sleep(2)


# ── Watchdog: pause stream when idle ─────────────────────────────────────────
def watchdog_thread():
    idle_since = None
    while True:
        time.sleep(15)
        now = time.time()
        with viewer_lock:
            stale = [k for k, t in active_viewers.items() if now - t > VIEWER_TTL]
            for k in stale:
                del active_viewers[k]
            count = len(active_viewers)

        if count > 0:
            idle_since = None
        else:
            if idle_since is None:
                idle_since = now
            elif now - idle_since >= IDLE_TIMEOUT and stream_active.is_set():
                print("No viewers — pausing stream")
                stream_active.clear()
                with _stream_lock:
                    for p in _stream_procs.values():
                        if p:
                            try:
                                p.kill()
                            except Exception:
                                pass


# ── Snapshot thread ───────────────────────────────────────────────────────────
def snapshot_thread():
    time.sleep(10)
    while True:
        time.sleep(SNAPSHOT_INTERVAL)
        if not stream_active.is_set():
            continue
        ts_files = sorted(HLS_DIR.glob("seg*.ts"))
        if len(ts_files) < 2:
            continue
        src = ts_files[-2]

        today   = datetime.now().strftime("%Y-%m-%d")
        ts      = datetime.now().strftime("%H%M%S")
        day_dir = SNAPSHOT_DIR / today
        day_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-vframes", "1", "-q:v", "2",
             str(day_dir / f"{ts}.jpg")],
            timeout=10, stderr=subprocess.DEVNULL,
        )


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
@auth.login_required
def index():
    return render_template("index.html")


@app.route("/hls/<path:filename>")
@auth.login_required
def hls(filename):
    mime = "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t"
    resp = send_from_directory(HLS_DIR, filename, mimetype=mime)
    resp.headers["Cache-Control"] = "no-cache, no-store"
    return resp


@app.route("/timelapse")
@auth.login_required
def timelapse_page():
    return render_template("timelapse.html")


@app.route("/api/heartbeat", methods=["POST"])
@auth.login_required
def heartbeat():
    sid = request.get_json(silent=True, force=True) or {}
    sid = str(sid.get("sid", ""))[:64]
    if sid:
        with viewer_lock:
            active_viewers[sid] = time.time()
        if not stream_active.is_set():
            stream_active.set()
    return "", 204


@app.route("/api/viewers")
@auth.login_required
def viewers():
    now = time.time()
    with viewer_lock:
        stale = [k for k, t in active_viewers.items() if now - t > VIEWER_TTL]
        for k in stale:
            del active_viewers[k]
        count = len(active_viewers)
    return jsonify({"count": count, "gen": stream_generation, "streaming": stream_active.is_set()})


@app.route("/api/snapshots")
@auth.login_required
def snapshots_index():
    if not SNAPSHOT_DIR.exists():
        return jsonify([])
    days = []
    for d in sorted(SNAPSHOT_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        jpgs = sorted(d.glob("*.jpg"))
        if not jpgs:
            continue
        noon = 120000
        pod  = min(jpgs, key=lambda f: abs(int(f.stem) - noon))
        days.append({"date": d.name, "count": len(jpgs), "pod": pod.name})
    return jsonify(days[:30])


@app.route("/api/timelapse")
@auth.login_required
def timelapse_list():
    if not TIMELAPSE_DIR.exists():
        return jsonify([])
    items = []
    for mp4 in sorted(TIMELAPSE_DIR.glob("*.mp4"), reverse=True):
        items.append({
            "date":    mp4.stem,
            "file":    mp4.name,
            "size_mb": round(mp4.stat().st_size / 1e6, 1),
        })
    return jsonify(items)


@app.route("/api/disk")
@auth.login_required
def disk_usage():
    def dir_mb(p):
        return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6, 1) if p.exists() else 0
    total, _, free = shutil.disk_usage("/")
    return jsonify({
        "snapshots_mb": dir_mb(SNAPSHOT_DIR),
        "timelapse_mb": dir_mb(TIMELAPSE_DIR),
        "free_gb":      round(free  / 1e9, 1),
        "total_gb":     round(total / 1e9, 1),
    })


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    HLS_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    TIMELAPSE_DIR.mkdir(parents=True, exist_ok=True)

    threading.Thread(target=capture_thread, daemon=True).start()
    threading.Thread(target=snapshot_thread, daemon=True).start()
    threading.Thread(target=watchdog_thread, daemon=True).start()

    app.run(host="0.0.0.0", port=PORT, threaded=True)
