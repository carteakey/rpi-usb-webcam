from flask import Flask, render_template, jsonify, request, send_from_directory, Response, redirect, url_for
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
import subprocess, os, threading, time, re, json, logging, argparse, shutil, copy
from datetime import datetime, timedelta
import socket
import psutil
from pathlib import Path
import configparser
import sys
import signal
from getpass import getpass

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('webcam_server.log')
    ]
)
logger = logging.getLogger('webcam-server')

# Create the Flask application
app = Flask(__name__)
auth = HTTPBasicAuth()

# Default configuration
DEFAULT_CONFIG = {
    'general': {
        'port': 8088,
        'host': '0.0.0.0',
        'debug': False,
        'snapshot_interval': 30,  # seconds
        'max_days_to_keep': 7,  # days to keep snapshots
    },
    'video': {
        'device': '/dev/video0',
        'resolution': '1280x720',
        'framerate': 30,
        'preset': 'ultrafast',
        'hls_time': 1,
        'hls_list_size': 5,
    },
    'audio': {
        'enabled': True,
        'device': 'hw:1,0',
        'sample_rate': 16000,
        'bit_rate': '96k',
    },
    'storage': {
        'snapshot_dir': 'static/snapshots',
        'hls_dir': 'static/hls',
        'timelapse_dir': 'static/timelapse',
    },
    'auth': {
        'username': 'admin',
        'password_hash': '',
    }
}

# Global config dictionary
config = copy.deepcopy(DEFAULT_CONFIG)

ENV_USERNAME = "WEBCAM_AUTH_USERNAME"
ENV_PASSWORD = "WEBCAM_AUTH_PASSWORD"
ENV_PASSWORD_HASH = "WEBCAM_AUTH_PASSWORD_HASH"


# Global process variables
ffmpeg_process = None
arecord_process = None
running = True
stream_starting = threading.Event()

def _stream_process_stderr(process, name, level=logging.ERROR):
    """Stream subprocess stderr lines into the application logger."""
    if not process or not process.stderr:
        return

    def _reader():
        try:
            for raw_line in iter(process.stderr.readline, b''):
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if line:
                    logger.log(level, f"{name}: {line}")
        except Exception:
            # Avoid tearing down the whole service if the pipe closes unexpectedly
            pass

    threading.Thread(target=_reader, daemon=True).start()

def load_config(config_file='config.ini', allow_empty_password=False):
    """Load configuration from file if it exists, otherwise create default"""
    global config
    config = copy.deepcopy(DEFAULT_CONFIG)

    if os.path.exists(config_file):
        logger.info(f"Loading configuration from {config_file}")
        parser = configparser.ConfigParser()
        parser.read(config_file)

        plain_password = None

        # Update config with values from file
        for section in parser.sections():
            if section not in config:
                config[section] = {}
            for key, value in parser.items(section):
                if section == 'auth':
                    normalized_key = key.lower()
                    if normalized_key == 'password':
                        plain_password = value.strip()
                        continue
                    if normalized_key == 'password_hash':
                        config['auth']['password_hash'] = value.strip()
                        continue
                # Handle specific types
                if key in ['port', 'snapshot_interval', 'framerate', 'hls_time',
                          'hls_list_size', 'max_days_to_keep']:
                    config[section][key] = int(value)
                elif key in ['debug', 'enabled']:
                    config[section][key] = parser.getboolean(section, key)
                else:
                    config[section][key] = value

        if plain_password and not config['auth']['password_hash']:
            config['auth']['password_hash'] = generate_password_hash(plain_password)
            parser.set('auth', 'password_hash', config['auth']['password_hash'])
            parser.remove_option('auth', 'password')
            with open(config_file, 'w') as f:
                parser.write(f)
            logger.warning("Migrated plain-text password in config.ini to a hashed password.")
    else:
        # Create default config file
        logger.info(f"Creating default configuration file at {config_file}")
        parser = configparser.ConfigParser()

        for section, values in config.items():
            parser[section] = {}
            for key, value in values.items():
                if section == 'auth' and key == 'password_hash':
                    parser[section][key] = ''
                else:
                    parser[section][key] = str(value)

        # Add default credentials but prompt user to change
        if 'auth' not in parser:
            parser['auth'] = {}
        parser['auth']['username'] = parser['auth'].get('username', config['auth']['username'] or 'admin')
        parser['auth']['password_hash'] = ''
        config['auth']['username'] = parser['auth']['username']
        config['auth']['password_hash'] = ''

        with open(config_file, 'w') as f:
            parser.write(f)

        logger.warning("Default configuration created without an admin password.")
        logger.warning("Set a password with `python app_v5.py --set-password` or by defining WEBCAM_AUTH_PASSWORD.")

    _apply_env_auth_overrides()

    if not allow_empty_password and not config['auth'].get('password_hash'):
        raise RuntimeError(
            "No admin password configured. Set WEBCAM_AUTH_PASSWORD / WEBCAM_AUTH_PASSWORD_HASH "
            "or run `python app_v5.py --set-password`."
        )

def _apply_env_auth_overrides():
    """Override username/password settings from environment variables."""
    env_username = os.getenv(ENV_USERNAME)
    if env_username:
        config['auth']['username'] = env_username.strip()
        logger.debug("Admin username overridden from environment variable.")

    env_password_hash = os.getenv(ENV_PASSWORD_HASH)
    env_password = os.getenv(ENV_PASSWORD)

    if env_password_hash:
        config['auth']['password_hash'] = env_password_hash.strip()
        logger.debug("Admin password hash loaded from environment variable.")
    elif env_password:
        config['auth']['password_hash'] = generate_password_hash(env_password)
        logger.debug("Admin password loaded from environment variable and hashed in memory.")

def save_config(config_file='config.ini'):
    """Save current configuration to file"""
    parser = configparser.ConfigParser()

    for section, values in config.items():
        parser[section] = {}
        for key, value in values.items():
            if section == 'auth' and key == 'password':
                continue
            parser[section][key] = str(value)

    with open(config_file, 'w') as f:
        parser.write(f)

    logger.info(f"Configuration saved to {config_file}")

def set_password_interactively(config_file):
    """Prompt the operator to set the admin password and persist the hash."""
    username = config['auth']['username']
    print(f"Updating password for admin user '{username}'. Press Ctrl+C to cancel.")

    for attempt in range(3):
        password = getpass("New admin password: ")
        if not password:
            logger.error("Password cannot be empty.")
            continue
        confirm_password = getpass("Confirm password: ")
        if password != confirm_password:
            logger.error("Passwords do not match. Try again.")
            continue

        config['auth']['password_hash'] = generate_password_hash(password)
        save_config(config_file)
        logger.info("Admin password updated.")
        return True

    logger.error("Failed to set password after multiple attempts.")
    return False

# Create required directories
def ensure_directories():
    """Ensure all required directories exist"""
    for section, values in config.items():
        for key, value in values.items():
            if key.endswith('_dir') and isinstance(value, str):
                os.makedirs(value, exist_ok=True)
                logger.debug(f"Ensured directory exists: {value}")

def is_stream_running():
    """Check if the ffmpeg process is currently streaming"""
    return ffmpeg_process is not None and ffmpeg_process.poll() is None

# Authentication
@auth.verify_password
def verify_password(username, password):
    """Verify username and password"""
    stored_username = config['auth']['username']
    stored_hash = config['auth'].get('password_hash')

    if not stored_hash:
        logger.error("Authentication attempted without a configured password hash.")
        return False

    if username == stored_username and check_password_hash(stored_hash, password):
        return username

# Find available webcams
def find_webcams():
    """Find available webcam devices"""
    devices = []
    try:
        for i in range(10):  # Check devices 0-9
            device_path = f"/dev/video{i}"
            if os.path.exists(device_path):
                # Try to get device info
                try:
                    result = subprocess.run(
                        ["v4l2-ctl", "--device", device_path, "--all"],
                        capture_output=True, text=True, timeout=1
                    )
                    if result.returncode == 0:
                        # Extract device name if possible
                        name_match = re.search(r"Card type:\s*(.+)", result.stdout)
                        name = name_match.group(1) if name_match else f"Video Device {i}"
                        devices.append({"path": device_path, "name": name})
                except (subprocess.SubprocessError, subprocess.TimeoutExpired):
                    # If v4l2-ctl fails, just add the device with a generic name
                    devices.append({"path": device_path, "name": f"Video Device {i}"})
    except Exception as e:
        logger.error(f"Error finding webcams: {e}")

    return devices

# Find audio devices
def find_audio_devices():
    """Find available audio input devices"""
    devices = []
    try:
        result = subprocess.run(["arecord", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith('card '):
                    match = re.search(r"card (\d+):.*?device (\d+): (.*?)$", line)
                    if match:
                        card, device, name = match.groups()
                        devices.append({
                            "path": f"hw:{card},{device}",
                            "name": name.strip()
                        })
    except Exception as e:
        logger.error(f"Error finding audio devices: {e}")

    return devices

# Helper to get available resolutions for a webcam
def get_available_resolutions(device_path):
    """Get available resolutions for a webcam device"""
    resolutions = []
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device_path, "--list-formats-ext"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            # Extract resolutions
            for line in result.stdout.splitlines():
                match = re.search(r"Size: Discrete (\d+x\d+)", line)
                if match:
                    resolution = match.group(1)
                    if resolution not in resolutions:
                        resolutions.append(resolution)
    except Exception as e:
        logger.error(f"Error getting resolutions for {device_path}: {e}")
        # Provide fallback resolutions
        resolutions = ["640x480", "1280x720", "1920x1080"]

    return resolutions

# Start snapshot capture loop
def snapshot_loop():
    """Periodically capture snapshots from the webcam"""
    while running:
        try:
            # Get current date for folder organization
            today = time.strftime("%Y-%m-%d")
            today_dir = os.path.join(config['storage']['snapshot_dir'], today)
            os.makedirs(today_dir, exist_ok=True)

            # Generate timestamp for filename
            timestamp = time.strftime("%H%M%S")
            output_path = os.path.join(today_dir, f"{timestamp}.jpg")

            # Check if HLS stream is running
            snapshot_taken = False
            if is_stream_running():
                # Snapshot from HLS stream
                logger.debug("Taking snapshot from HLS stream")
                subprocess.run([
                    "ffmpeg", "-y", "-i",
                    os.path.join(config['storage']['hls_dir'], "stream.m3u8"),
                    "-frames:v", "1", output_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                snapshot_taken = True
            elif stream_starting.is_set():
                logger.debug("Skipping snapshot while stream is restarting")
            else:
                # Snapshot directly from webcam
                logger.debug("Taking snapshot directly from webcam")
                subprocess.run([
                    "ffmpeg", "-y",
                    "-f", "v4l2", "-video_size", config['video']['resolution'],
                    "-i", config['video']['device'],
                    "-vframes", "1", output_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                snapshot_taken = True

            if snapshot_taken:
                logger.debug(f"Snapshot saved to {output_path}")

            # Cleanup old snapshots
            cleanup_old_snapshots()

        except Exception as e:
            logger.error(f"Error in snapshot loop: {e}")

        # Sleep until next snapshot
        time.sleep(config['general']['snapshot_interval'])

# Clean up old snapshots
def cleanup_old_snapshots():
    """Remove snapshots older than the configured number of days"""
    try:
        max_age = config['general']['max_days_to_keep']
        cutoff_date = datetime.now() - timedelta(days=max_age)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")

        snapshot_dir = config['storage']['snapshot_dir']
        for date_dir in os.listdir(snapshot_dir):
            # Only process directory names that look like dates
            if re.match(r"^\d{4}-\d{2}-\d{2}$", date_dir):
                if date_dir < cutoff_str:
                    dir_path = os.path.join(snapshot_dir, date_dir)
                    logger.info(f"Removing old snapshots from {dir_path}")
                    shutil.rmtree(dir_path)
    except Exception as e:
        logger.error(f"Error cleaning up old snapshots: {e}")

# Start HLS streaming
def start_hls_stream():
    """Start the HLS video stream with optional audio"""
    global ffmpeg_process, arecord_process

    if stream_starting.is_set():
        logger.warning("Stream start requested while another start is in progress")
        return False

    stream_starting.set()
    try:
        # Ensure HLS directory exists
        os.makedirs(config['storage']['hls_dir'], exist_ok=True)
        hls_dir = Path(config['storage']['hls_dir'])

        # Remove stale HLS artifacts so clients don't see empty segments
        try:
            for segment in hls_dir.glob("stream*.ts"):
                segment.unlink(missing_ok=True)
            (hls_dir / "stream.m3u8").unlink(missing_ok=True)
        except Exception as cleanup_error:
            logger.warning(f"Unable to clear old HLS files: {cleanup_error}")

        # Kill any existing processes
        stop_streaming()

        logger.info(f"Starting HLS stream at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Video settings
        video_device = config['video']['device']
        resolution = config['video']['resolution']
        framerate = config['video']['framerate']
        preset = config['video']['preset']
        hls_time = config['video']['hls_time']
        hls_list_size = config['video']['hls_list_size']

        ffmpeg_output_path = os.path.join(config['storage']['hls_dir'], "stream.m3u8")

        # Base ffmpeg command
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-loglevel", "error"
        ]

        # Track whether we actually have an audio process feeding ffmpeg
        active_audio_process = None

        if config['audio']['enabled']:
            audio_device = config['audio']['device']
            sample_rate = config['audio']['sample_rate']
            bit_rate = config['audio']['bit_rate']

            arecord_cmd = [
                "arecord",
                "-D", audio_device,
                "-f", "S16_LE",
                "-c1",
                "-r", str(sample_rate)
            ]

            try:
                logger.info(f"Starting audio capture: {' '.join(arecord_cmd)}")
                arecord_process = subprocess.Popen(
                    arecord_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

                # Give arecord a moment to fail fast if the device is unavailable
                time.sleep(0.2)
                if arecord_process.poll() is not None:
                    stderr_output = ""
                    try:
                        _, stderr_bytes = arecord_process.communicate(timeout=0.1)
                        stderr_output = stderr_bytes.decode("utf-8", errors="ignore").strip()
                    except Exception:
                        pass
                    logger.error(f"Audio capture exited immediately with code {arecord_process.returncode}. {stderr_output}")
                    arecord_process = None
                else:
                    active_audio_process = arecord_process
                    _stream_process_stderr(active_audio_process, "arecord")
                    ffmpeg_cmd.extend([
                        "-f", "s16le",
                        "-ar", str(sample_rate),
                        "-ac", "1",
                        "-i", "pipe:0"
                    ])
            except Exception as e:
                logger.error(f"Failed to start audio capture: {e}")
                if arecord_process:
                    arecord_process.terminate()
                    try:
                        arecord_process.wait(timeout=1)
                    except Exception:
                        pass
                    arecord_process = None

        if not active_audio_process and config['audio']['enabled']:
            logger.warning("Audio capture unavailable; falling back to video-only stream")

        # Append video input and output settings
        ffmpeg_cmd.extend([
            "-f", "v4l2",
            "-framerate", str(framerate),
            "-video_size", resolution,
            "-i", video_device,
            "-c:v", "libx264",
            "-preset", preset,
            "-tune", "zerolatency",
            "-x264opts", f"keyint={framerate}:min-keyint={framerate}:scenecut=0",
        ])

        if active_audio_process:
            ffmpeg_cmd.extend([
                "-c:a", "aac",
                "-b:a", bit_rate
            ])

        ffmpeg_cmd.extend([
            "-f", "hls",
            "-hls_time", str(hls_time),
            "-hls_list_size", str(hls_list_size),
            "-hls_flags", "delete_segments+append_list+omit_endlist",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            ffmpeg_output_path
        ])

        logger.info(f"Starting ffmpeg{' with audio' if active_audio_process else ''}: {' '.join(ffmpeg_cmd)}")

        ffmpeg_process = subprocess.Popen(
            ffmpeg_cmd,
            stdin=active_audio_process.stdout if active_audio_process else None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )

        if active_audio_process:
            # Close our copy of the stdout pipe so ffmpeg is the only reader
            active_audio_process.stdout.close()

        # Give ffmpeg a moment to report any immediate failure
        time.sleep(0.5)
        if ffmpeg_process.poll() is not None:
            stderr_output = ""
            try:
                stderr_output = ffmpeg_process.stderr.read().decode("utf-8", errors="ignore").strip()
            except Exception:
                pass
            logger.error(f"ffmpeg exited immediately with code {ffmpeg_process.returncode}. {stderr_output}")
            stop_streaming()
            return False
        else:
            _stream_process_stderr(ffmpeg_process, "ffmpeg")

        logger.info("HLS stream started successfully")
        return True

    except Exception as e:
        logger.error(f"Error starting HLS stream: {e}")
        stop_streaming()
        return False
    finally:
        stream_starting.clear()

# Stop streaming
def stop_streaming():
    """Stop all streaming processes"""
    global ffmpeg_process, arecord_process

    try:
        if ffmpeg_process:
            logger.info("Stopping ffmpeg process")
            ffmpeg_process.terminate()
            try:
                ffmpeg_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("ffmpeg did not exit in time; killing process")
                ffmpeg_process.kill()
                ffmpeg_process.wait(timeout=5)
            ffmpeg_process = None
    except Exception as e:
        logger.error(f"Error stopping ffmpeg: {e}")

    try:
        if arecord_process:
            logger.info("Stopping audio capture")
            arecord_process.terminate()
            try:
                arecord_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Audio capture process did not exit in time; killing process")
                arecord_process.kill()
                arecord_process.wait(timeout=5)
            arecord_process = None
    except Exception as e:
        logger.error(f"Error stopping audio: {e}")

# Restart streaming
def restart_streaming():
    """Restart the streaming processes"""
    return start_hls_stream()

# Generate timelapse from snapshots
def generate_timelapse(date_str=None):
    """Generate a timelapse video from snapshots"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    try:
        snapshot_dir = os.path.join(config['storage']['snapshot_dir'], date_str)
        output_dir = config['storage']['timelapse_dir']

        # Make sure directories exist
        if not os.path.exists(snapshot_dir):
            logger.error(f"Snapshot directory not found: {snapshot_dir}")
            return False

        os.makedirs(output_dir, exist_ok=True)

        # Check if there are any snapshots
        snapshots = [f for f in os.listdir(snapshot_dir) if f.endswith('.jpg')]
        if not snapshots:
            logger.error(f"No snapshots found in {snapshot_dir}")
            return False

        # Generate output filename
        output_file = os.path.join(output_dir, f"{date_str}_timelapse.mp4")

        # Run ffmpeg to create timelapse
        logger.info(f"Generating timelapse for {date_str}")
        result = subprocess.run([
            "ffmpeg",
            "-y",  # Overwrite output
            "-pattern_type", "glob",
            "-i", f"{snapshot_dir}/*.jpg",
            "-c:v", "libx264",
            "-vf", "fps=10,format=yuv420p",
            "-movflags", "+faststart",
            output_file
        ], capture_output=True, text=True)

        if result.returncode == 0:
            logger.info(f"Timelapse created: {output_file}")
            return True
        else:
            logger.error(f"Failed to create timelapse: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"Error generating timelapse: {e}")
        return False

# Get list of available timelapse videos
def get_timelapse_list():
    """Get list of available timelapse videos"""
    try:
        files = sorted(
            f for f in os.listdir(config['storage']['timelapse_dir'])
            if f.lower().endswith((".mp4", ".mkv"))
        )
        return [f"/static/timelapse/{f}" for f in files]
    except Exception as e:
        logger.error(f"Error getting timelapse list: {e}")
        return []

# Get list of available snapshot dates
def get_snapshot_dates():
    """Get list of available snapshot dates"""
    try:
        snapshot_dir = config['storage']['snapshot_dir']
        dates = [d for d in os.listdir(snapshot_dir)
                if os.path.isdir(os.path.join(snapshot_dir, d)) and
                re.match(r"^\d{4}-\d{2}-\d{2}$", d)]
        return sorted(dates, reverse=True)
    except Exception as e:
        logger.error(f"Error getting snapshot dates: {e}")
        return []

# Get snapshots for a specific date
def get_snapshots_for_date(date_str, interval="hour"):
    """Get snapshots for a specific date with specified interval"""
    try:
        snapshot_dir = os.path.join(config['storage']['snapshot_dir'], date_str)

        if not os.path.exists(snapshot_dir):
            logger.error(f"Snapshot directory not found: {snapshot_dir}")
            return []

        # Get all jpg files
        files = sorted([f for f in os.listdir(snapshot_dir) if f.endswith('.jpg')])

        if not files:
            return []

        selected = []

        if interval == "hour":
            # One snapshot per hour
            seen_hours = set()
            for f in files:
                # Extract hour safely
                hour = f[0:2]
                if hour not in seen_hours:
                    selected.append(f"/static/snapshots/{date_str}/{f}")
                    seen_hours.add(hour)
        elif interval == "all":
            # All snapshots
            selected = [f"/static/snapshots/{date_str}/{f}" for f in files]
        elif interval == "sample":
            # Sample a reasonable number (max 24)
            step = max(1, len(files) // 24)
            selected = [f"/static/snapshots/{date_str}/{files[i]}"
                       for i in range(0, len(files), step)]

        return selected

    except Exception as e:
        logger.error(f"Error getting snapshots for date: {e}")
        return []

# Get system information
def get_system_info():
    """Get system information"""
    try:
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)

        # CPU usage
        cpu_percent = psutil.cpu_percent(interval=0.1)

        # Memory usage
        memory = psutil.virtual_memory()
        memory_percent = memory.percent

        # Disk usage
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent

        # Uptime
        uptime = time.time() - psutil.boot_time()
        uptime_str = str(timedelta(seconds=int(uptime)))

        # Temperature (Raspberry Pi specific)
        temperature = None
        try:
            if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temperature = float(f.read().strip()) / 1000.0
        except:
            pass

        # Video stream status
        stream_running = is_stream_running()

        # Snapshot information
        snapshot_dates = get_snapshot_dates()
        latest_snapshot = None
        if snapshot_dates:
            latest_date = snapshot_dates[0]
            snapshots = get_snapshots_for_date(latest_date)
            if snapshots:
                latest_snapshot = snapshots[-1]

        # Timelapse information
        timelapse_count = len(get_timelapse_list())

        return {
            "hostname": hostname,
            "ip_address": ip_address,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "disk_percent": disk_percent,
            "uptime": uptime_str,
            "temperature": temperature,
            "stream_running": stream_running,
            "snapshot_date_count": len(snapshot_dates),
            "latest_snapshot": latest_snapshot,
            "timelapse_count": timelapse_count,
            "version": "4.0.0"
        }

    except Exception as e:
        logger.error(f"Error getting system info: {e}")
        return {
            "error": str(e),
            "version": "4.0.0"
        }

# Flask routes
@app.route('/')
@auth.login_required
def index():
    """Main application page"""
    system_info = get_system_info()
    return render_template('index.html', system_info=system_info)

@app.route('/snapshots')
@auth.login_required
def list_snapshots():
    """List snapshots for today with hourly interval"""
    today = time.strftime("%Y-%m-%d")
    interval = request.args.get('interval', 'hour')
    return jsonify(get_snapshots_for_date(today, interval))

@app.route('/snapshots/<date>')
@auth.login_required
def list_snapshots_by_date(date):
    """List snapshots for specific date"""
    interval = request.args.get('interval', 'hour')
    return jsonify(get_snapshots_for_date(date, interval))

@app.route('/snapshot_dates')
@auth.login_required
def list_snapshot_dates():
    """List available snapshot dates"""
    return jsonify(get_snapshot_dates())

@app.route('/timelapses')
@auth.login_required
def list_timelapses():
    """List available timelapses"""
    return jsonify(get_timelapse_list())

@app.route('/api/generate_timelapse', methods=['POST'])
@auth.login_required
def api_generate_timelapse():
    """API endpoint to generate a timelapse"""
    date = request.json.get('date')
    if not date:
        date = time.strftime("%Y-%m-%d")

    success = generate_timelapse(date)
    return jsonify({"success": success})

@app.route('/api/stream_control', methods=['POST'])
@auth.login_required
def api_stream_control():
    """API endpoint to control the stream"""
    action = request.json.get('action')

    if action == 'start':
        success = start_hls_stream()
    elif action == 'stop':
        stop_streaming()
        success = True
    elif action == 'restart':
        success = restart_streaming()
    else:
        return jsonify({"success": False, "error": "Invalid action"})

    return jsonify({"success": success})

@app.route('/api/settings', methods=['GET', 'POST'])
@auth.login_required
def api_settings():
    """API endpoint to get or update settings"""
    if request.method == 'GET':
        # Return current settings (excluding password hash)
        settings = {}
        for section, values in config.items():
            settings[section] = {}
            for key, value in values.items():
                if key != 'password_hash':
                    settings[section][key] = value
        return jsonify(settings)
    else:
        # Update settings
        try:
            data = request.json
            updated = False

            for section, values in data.items():
                if section in config:
                    for key, value in values.items():
                        # Special handling for password
                        if section == 'auth' and key == 'password':
                            if value:  # Only update if not empty
                                config['auth']['password_hash'] = generate_password_hash(value)
                                updated = True
                        elif key in config[section]:
                            # Convert types as needed
                            if isinstance(config[section][key], bool):
                                config[section][key] = bool(value)
                            elif isinstance(config[section][key], int):
                                config[section][key] = int(value)
                            else:
                                config[section][key] = value
                            updated = True

            if updated:
                save_config()
                # Apply certain changes immediately
                if 'video' in data or 'audio' in data:
                    restart_streaming()

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating settings: {e}")
            return jsonify({"success": False, "error": str(e)})

@app.route('/api/system', methods=['GET'])
@auth.login_required
def api_system():
    """API endpoint to get system information"""
    return jsonify(get_system_info())

@app.route('/api/devices', methods=['GET'])
@auth.login_required
def api_devices():
    """API endpoint to get available devices"""
    webcams = find_webcams()
    audio_devices = find_audio_devices()

    # Get resolutions for the current video device
    current_device = config['video']['device']
    resolutions = get_available_resolutions(current_device)

    return jsonify({
        "webcams": webcams,
        "audio_devices": audio_devices,
        "resolutions": resolutions
    })

@app.route('/api/resolutions', methods=['GET'])
@auth.login_required
def api_resolutions():
    """API endpoint to get available resolutions for a device"""
    device = request.args.get('device', config['video']['device'])
    resolutions = get_available_resolutions(device)
    return jsonify(resolutions)

# Handle graceful shutdown
def signal_handler(sig, frame):
    """Handle termination signals"""
    global running
    logger.info("Shutdown signal received, cleaning up...")
    running = False
    stop_streaming()
    sys.exit(0)

# Main application
if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Webcam Server with HLS streaming')
    parser.add_argument('--port', type=int, help='Port to run the server on')
    parser.add_argument('--host', type=str, help='Host to bind the server to')
    parser.add_argument('--config', type=str, default='config.ini', help='Path to config file')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--set-password', action='store_true', help='Interactively set admin password and exit')
    args = parser.parse_args()

    # Load configuration
    try:
        load_config(args.config, allow_empty_password=args.set_password)
    except RuntimeError as auth_error:
        logger.error(auth_error)
        sys.exit(1)

    if args.set_password:
        success = set_password_interactively(args.config)
        sys.exit(0 if success else 1)

    # Override config with command line arguments
    if args.port:
        config['general']['port'] = args.port
    if args.host:
        config['general']['host'] = args.host
    if args.debug:
        config['general']['debug'] = True
        logger.setLevel(logging.DEBUG)

    # Ensure required directories exist
    ensure_directories()

    # Start background tasks
    logger.info("Starting snapshot capture thread")
    threading.Thread(target=snapshot_loop, daemon=True).start()

    # Start HLS stream
    logger.info("Starting HLS stream")
    start_hls_stream()

    # Start Flask application
    logger.info(f"Starting web server on {config['general']['host']}:{config['general']['port']}")
    app.run(
        host=config['general']['host'],
        port=config['general']['port'],
        debug=config['general']['debug'],
        use_reloader=False  # Disable reloader to prevent duplicate processes
    )
