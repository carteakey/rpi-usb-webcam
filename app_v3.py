from flask import Flask, send_from_directory, render_template, jsonify
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
import subprocess, os, threading, time, re

app = Flask(__name__)
auth = HTTPBasicAuth()

# Configure simple users
users = {
    "khushi": generate_password_hash("hasija")  # <-- Change this
}

TIME_LAPSE_ROOT = "static/timelapse"
os.makedirs(TIME_LAPSE_ROOT, exist_ok=True)

@auth.verify_password
def verify_password(username, password):
    if username in users and check_password_hash(users.get(username), password):
        return username

SNAPSHOT_ROOT = "static/snapshots"
os.makedirs(SNAPSHOT_ROOT, exist_ok=True)



# --- new helper to list finished mp4 files ---
def _get_timelapse_list():
    files = sorted(
        f for f in os.listdir(TIME_LAPSE_ROOT)
        if f.lower().endswith((".mp4", ".mkv"))
    )
    return [f"/static/timelapse/{f}" for f in files]


def snapshot_loop():
    while True:
        today = time.strftime("%Y-%m-%d")
        today_dir = os.path.join(SNAPSHOT_ROOT, today)
        os.makedirs(today_dir, exist_ok=True)

        timestamp = time.strftime("%H%M%S")
        output_path = os.path.join(today_dir, f"{timestamp}.jpg")

        if os.path.exists("static/hls/stream.m3u8"):
            # Stream running, snapshot from HLS
            subprocess.run([
                "ffmpeg", "-y", "-i", "static/hls/stream.m3u8",
                "-frames:v", "1", output_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # No stream, snapshot directly from webcam
            subprocess.run([
                "ffmpeg", "-y",
                "-f", "v4l2", "-video_size", "640x480", "-i", "/dev/video0",
                "-vframes", "1", output_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        time.sleep(30)


def find_audio_device():
    return None
    try:
        result = subprocess.check_output(["arecord", "-l"], text=True)
        for line in result.splitlines():
            match = re.search(r"card (\d+): .*C270.*device (\d+):", line)
            if match:
                card, device = match.groups()
                return f"hw:{card},{device}"
    except Exception as e:
        print("Audio detection failed:", e)
    return None


def start_hls_stream():
    os.makedirs("static/hls", exist_ok=True)

    arecord = subprocess.Popen([
        "arecord", "-D", "hw:0,0", "-f", "S16_LE", "-c1", "-r", "16000"
    ], stdout=subprocess.PIPE)

    # Start FFmpeg directly, capture both video and audio via ALSA
    ffmpeg = subprocess.Popen([
        "ffmpeg",
         "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "pipe:0",
        "-f", "v4l2", "-framerate", "30", "-video_size", "1280x720", "-i", "/dev/video0",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-x264opts", "keyint=30:min-keyint=30:scenecut=-1",
        "-c:a", "aac", "-b:a", "96k",
        "-f", "hls",
        "-hls_time", "1",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments+append_list",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "static/hls/stream.m3u8"
    ],  stdin=arecord.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # Optional: redirect stderr to a log file
    
@app.route('/')
@auth.login_required
def index():
    return render_template('index.html')

@app.route('/snapshots')
@auth.login_required
def list_snapshots():
    today = time.strftime("%Y-%m-%d")
    today_dir = os.path.join(SNAPSHOT_ROOT, today)

    if not os.path.exists(today_dir):
        return jsonify([])

    # Only pick real jpg files
    files = sorted([f for f in os.listdir(today_dir) if f.endswith('.jpg')])

    if not files:
        return jsonify([])

    selected = []
    seen_hours = set()

    for f in files:
        # Extract hour safely
        hour = f[0:2]
        if hour not in seen_hours:
            selected.append(f"/static/snapshots/{today}/{f}")
            seen_hours.add(hour)

    return jsonify(selected)


# --- new route ---
@app.route("/timelapses")
@auth.login_required
def list_timelapses():
    return jsonify(_get_timelapse_list())


if __name__ == "__main__":
    threading.Thread(target=snapshot_loop, daemon=True).start()
    start_hls_stream()
    app.run(host="0.0.0.0", port=8088)
