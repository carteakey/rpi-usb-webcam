# Setup Notes — rpi-usb-webcam

This documents the setup, fixes, and quirks discovered when deploying on `fx505` (CachyOS / Arch Linux).

## Environment

- **Machine:** fx505 (not a Raspberry Pi — standard x86 Linux)
- **OS:** CachyOS (Arch-based)
- **Port:** 8089
- **Service:** systemd (`cam_server.service`)
- **Webcam:** Logitech C270 HD at `/dev/video2`
- **Audio:** Webcam mic at `hw:2,0` (card 2, ALSA)

## Initial Setup (2026-03-09)

### 1. Recreate the venv

The `.venv` was broken (pip not installed). Recreated it:

```bash
python3 -m venv --clear .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Install missing system dependency

`ffmpeg` on this system is linked against `libsndio.so.7`, which wasn't installed. Even video-only streams fail without it:

```bash
sudo pacman -S sndio
```

### 3. Fix audio device

`config.ini` had `device = hw:0,0` which doesn't exist. Available devices:

```
card 1: HD-Audio Generic (ALC256) → hw:1,0  (laptop mic)
card 2: C270 HD WEBCAM (USB Audio) → hw:2,0  (webcam mic)
```

Updated `config.ini` to `device = hw:2,0`.

### 4. Add missing `/hls/` Flask route

The template requests `/hls/stream.m3u8` but no route existed for it. Flask's default static serving is at `/static/hls/`. Added to `app_v5.py`:

```python
@app.route('/hls/<path:filename>')
@auth.login_required
def serve_hls(filename):
    return send_from_directory(config['storage']['hls_dir'], filename)
```

### 5. Restore missing UI tabs

Commit `bde115f` replaced the full Bootstrap UI (with Live / Snapshots / Timelapses / Settings / System tabs) with a minimal dark full-screen UI, losing Settings and System views. Restored them as slide-in panels in the same dark glass aesthetic:

- **Snapshot button** (camera icon, title bar) → right slide-in panel
- **System button** (activity icon, title bar) → left slide-in panel with CPU/mem/disk/stream controls
- **Settings button** (gear icon, controls) → left slide-in panel with Video/Audio/Storage/Auth sub-tabs
- **Timelapse** → accessible via "view timelapses →" link in snapshot panel → `/timelapse` page

## Service Management

```bash
sudo systemctl start cam_server
sudo systemctl stop cam_server
sudo systemctl restart cam_server
systemctl status cam_server
journalctl -u cam_server -f
```

## Troubleshooting

### Stream not starting
The app uses an idle-stream watchdog: stream pauses after 120s with no viewers, restarts on heartbeat. If the browser shows "connecting", it's waiting for the stream to start. The UI polls `/api/viewers` and inits HLS only once `streaming: true`.

### ffmpeg fails to load
```
ffmpeg: error while loading shared libraries: libsndio.so.7
```
Fix: `sudo pacman -S sndio`

### Audio not working
Check devices with `arecord -l`. Update `device` under `[audio]` in `config.ini` to match the correct card.

### Port already in use
Check: `ss -tlnp | grep 8089`. Kill stale process or restart service.

## Config

```ini
[general]
port = 8089

[video]
device = /dev/video2
resolution = 1280x720
framerate = 30
preset = medium

[audio]
enabled = True
device = hw:2,0

[auth]
username = kchauhan
password_hash = <bcrypt hash>
```
