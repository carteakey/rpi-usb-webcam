from flask import Flask, render_template, jsonify, request, send_from_directory, Response, redirect, url_for
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
import subprocess, os, threading, time, re, json, logging, argparse, shutil
from datetime import datetime, timedelta
import socket
import psutil
from pathlib import Path
import configparser
import sys
import signal

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
        'username': 'khushi',
        # Default password is 'password' - this will be overridden by config file
        'password_hash': generate_password_hash('hasija'),
    }
}

# Global config dictionary
config = dict(DEFAULT_CONFIG)

# Global process variables
ffmpeg_process = None
arecord_process = None
running = True

def load_config(config_file='config.ini'):
    """Load configuration from file if it exists, otherwise create default"""
    global config

    if os.path.exists(config_file):
        logger.info(f"Loading configuration from {config_file}")
        parser = configparser.ConfigParser()
        parser.read(config_file)

        # Update config with values from file
        for section in parser.sections():
            if section not in config:
                config[section] = {}
            for key, value in parser.items(section):
                # Handle specific types
                if key in ['port', 'snapshot_interval', 'framerate', 'hls_time',
                          'hls_list_size', 'max_days_to_keep']:
                    config[section][key] = int(value)
                elif key in ['debug', 'enabled']:
                    config[section][key] = parser.getboolean(section, key)
                else:
                    config[section][key] = value
    else:
        # Create default config file
        logger.info(f"Creating default configuration file at {config_file}")
        parser = configparser.ConfigParser()

        for section, values in config.items():
            parser[section] = {}
            for key, value in values.items():
                # Skip password hash in the saved config
                if key != 'password_hash':
                    parser[section][key] = str(value)

        # Add default credentials but prompt user to change
        parser['auth']['username'] = 'admin'
        parser['auth']['password'] = 'change_this_password'

        with open(config_file, 'w') as f:
            parser.write(f)

        logger.warning("Default configuration created. Please edit config.ini to set your username and password.")

def save_config(config_file='config.ini'):
    """Save current configuration to file"""
    parser = configparser.ConfigParser()

    for section, values in config.items():
        parser[section] = {}
        for key, value in values.items():
            # Skip password hash in the saved config
            if key != 'password_hash' and key != 'password':
                parser[section][key] = str(value)

    with open(config_file, 'w') as f:
        parser.write(f)

    logger.info(f"Configuration saved to {config_file}")

# Create required directories
def ensure_directories():
    """Ensure all required directories exist"""
    for section, values in config.items():
        for key, value in values.items():
            if key.endswith('_dir') and isinstance(value, str):
                os.makedirs(value, exist_ok=True)
                logger.debug(f"Ensured directory exists: {value}")

# Authentication
@auth.verify_password
def verify_password(username, password):
    """Verify username and password"""
    stored_username = config['auth']['username']
    stored_hash = config['auth']['password_hash']

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
            if os.path.exists(os.path.join(config['storage']['hls_dir'], "stream.m3u8")):
                # Snapshot from HLS stream
                logger.debug("Taking snapshot from HLS stream")
                subprocess.run([
                    "ffmpeg", "-y", "-i",
                    os.path.join(config['storage']['hls_dir'], "stream.m3u8"),
                    "-frames:v", "1", output_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # Snapshot directly from webcam
                logger.debug("Taking snapshot directly from webcam")
                subprocess.run([
                    "ffmpeg", "-y",
                    "-f", "v4l2", "-video_size", config['video']['resolution'],
                    "-i", config['video']['device'],
                    "-vframes", "1", output_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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

    try:
        # Ensure HLS directory exists
        os.makedirs(config['storage']['hls_dir'], exist_ok=True)

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

        # Build the ffmpeg command
        ffmpeg_cmd = [
            "ffmpeg",
            "-f", "v4l2",
            "-framerate", str(framerate),
            "-video_size", resolution,
            "-i", video_device,
            "-c:v", "libx264",
            "-preset", preset,
            "-tune", "zerolatency",
            "-x264opts", f"keyint={framerate}:min-keyint={framerate}:scenecut=0",
            "-f", "hls",
            "-hls_time", str(hls_time),
            "-hls_list_size", str(hls_list_size),
            "-hls_flags", "delete_segments+append_list+omit_endlist",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            os.path.join(config['storage']['hls_dir'], "stream.m3u8")
        ]

        # Add audio if enabled
        if config['audio']['enabled']:
            audio_device = config['audio']['device']
            sample_rate = config['audio']['sample_rate']
            bit_rate = config['audio']['bit_rate']

            try:
                # Start audio capture
                arecord_cmd = [
                    "arecord",
                    "-D", audio_device,
                    "-f", "S16_LE",
                    "-c1",
                    "-r", str(sample_rate)
                ]
                logger.info(f"Starting audio capture: {' '.join(arecord_cmd)}")
                arecord_process = subprocess.Popen(
                    arecord_cmd,
                    stdout=subprocess.PIPE
                )

                # Insert audio parameters into ffmpeg command
                ffmpeg_cmd[1:1] = [
                    "-f", "s16le",
                    "-ar", str(sample_rate),
                    "-ac", "1",
                    "-i", "pipe:0"
                ]

                # Add audio codec parameters
                ffmpeg_cmd.insert(-1, "-c:a")
                ffmpeg_cmd.insert(-1, "aac")
                ffmpeg_cmd.insert(-1, "-b:a")
                ffmpeg_cmd.insert(-1, bit_rate)

                # Start ffmpeg with audio input from arecord
                logger.info(f"Starting ffmpeg with audio: {' '.join(ffmpeg_cmd)}")
                ffmpeg_process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdin=arecord_process.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                logger.error(f"Failed to start audio capture: {e}")
                # Fall back to video-only if audio fails
                if arecord_process:
                    arecord_process.terminate()
                    arecord_process = None

                logger.info("Falling back to video-only stream")
                ffmpeg_process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
        else:
            # Video only
            logger.info(f"Starting video-only stream: {' '.join(ffmpeg_cmd)}")
            ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        logger.info("HLS stream started successfully")
        return True

    except Exception as e:
        logger.error(f"Error starting HLS stream: {e}")
        stop_streaming()
        return False

# Stop streaming
def stop_streaming():
    """Stop all streaming processes"""
    global ffmpeg_process, arecord_process

    try:
        if ffmpeg_process:
            logger.info("Stopping ffmpeg process")
            ffmpeg_process.terminate()
            ffmpeg_process.wait(timeout=5)
            ffmpeg_process = None
    except Exception as e:
        logger.error(f"Error stopping ffmpeg: {e}")

    try:
        if arecord_process:
            logger.info("Stopping audio capture")
            arecord_process.terminate()
            arecord_process.wait(timeout=5)
            arecord_process = None
    except Exception as e:
        logger.error(f"Error stopping audio: {e}")

# Restart streaming
def restart_streaming():
    """Restart the streaming processes"""
    stop_streaming()
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
        stream_running = ffmpeg_process is not None and ffmpeg_process.poll() is None

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
    args = parser.parse_args()

    # Load configuration
    load_config(args.config)

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
