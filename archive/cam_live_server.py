# cam_server.py
from flask import Flask, Response, send_file
import subprocess, os, threading, time

app = Flask(__name__)
STREAM_ACTIVE = False
SNAPSHOT_PATH = "./timelapse"
os.makedirs(SNAPSHOT_PATH, exist_ok=True)

def snapshot_loop():
    while True:
        if not STREAM_ACTIVE:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            subprocess.run([
                "ffmpeg", "-f", "v4l2", "-video_size", "640x480", "-i", "/dev/video0",
                "-vframes", "1", f"{SNAPSHOT_PATH}/{timestamp}.jpg"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(30)

@app.route('/')
def latest_snapshot():
    try:
        files = sorted(os.listdir(SNAPSHOT_PATH))
        return send_file(f"{SNAPSHOT_PATH}/{files[-1]}", mimetype='image/jpeg')
    except:
        return "No images yet."

@app.route('/live')
def live():
    global STREAM_ACTIVE
    STREAM_ACTIVE = True

    def generate():
        cmd = [
            "ffmpeg",
            "-f", "v4l2", "-i", "/dev/video0",
            "-f", "alsa", "-i", "default",  # Change to your mic device if needed
            "-f", "mpegts", "-codec:v", "mpeg1video", "-b:v", "800k",
            "-codec:a", "mp2", "-b:a", "128k",
            "-"
        ]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        try:
            while True:
                data = p.stdout.read(1024)
                if not data:
                    break
                yield data
        finally:
            p.kill()
            global STREAM_ACTIVE
            STREAM_ACTIVE = False

    return Response(generate(), mimetype='video/mp2t')

if __name__ == "__main__":
    threading.Thread(target=snapshot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8088)
