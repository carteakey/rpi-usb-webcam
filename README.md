# Raspberry Pi USB Webcam Server

A lightweight, feature-rich web server for streaming USB webcam video on Raspberry Pi. Transform your Raspberry Pi and any USB webcam into a complete surveillance and monitoring solution with live streaming, automatic snapshots, and timelapse video generation.

## Overview

This project provides a Flask-based web application that enables real-time video streaming from USB webcams connected to a Raspberry Pi. It's designed for home security, wildlife monitoring, time-lapse photography, or any scenario requiring remote camera access and automated capture.

**Key Capabilities:**
- 📹 Real-time HLS video streaming with low latency
- 🎤 Audio support for webcams with built-in microphones
- 📸 Automatic snapshot capture at configurable intervals
- 🎬 Timelapse video generation from captured snapshots
- 📱 Responsive web interface accessible from any device
- 🔒 Secure HTTP Basic Authentication
- ⚙️ Web-based configuration interface
- 📊 System monitoring and statistics dashboard
- 🐳 Docker support for easy deployment
- 🔄 Systemd service integration for automatic startup

## How It Works

The application uses FFmpeg to capture video from USB webcams and encode it into HLS (HTTP Live Streaming) format for web delivery. The Flask web server handles authentication, serves the video stream, manages snapshots, and provides a user-friendly interface for configuration and monitoring.

**Architecture:**
1. **Video Capture**: FFmpeg captures video (and optionally audio) from the USB webcam
2. **HLS Encoding**: Video is encoded in real-time to HLS format for efficient streaming
3. **Snapshot Service**: Background thread captures periodic snapshots for archival
4. **Web Interface**: Flask serves a responsive web UI for viewing and controlling the camera
5. **Configuration**: All settings can be adjusted through the web interface or config file

## Features

- Live video streaming using HLS protocol
- Audio support (if webcam has a microphone)
- Automatic snapshot capture at regular intervals
- Timelapse video generation from snapshots
- Mobile-friendly responsive web interface
- Protected access with HTTP Basic Authentication
- Systemd service for automatic startup
- Automatic device detection
- Multiple video resolution support
- Configurable settings through web interface
- System monitoring and statistics

## Prerequisites

**Hardware Requirements:**
- Raspberry Pi (tested on Pi 3, Pi 4, and Pi Zero 2W)
- USB webcam (any V4L2-compatible device)
- MicroSD card (16GB+ recommended)
- Stable power supply

**Software Requirements:**
- Raspberry Pi OS (Bullseye or later recommended)
- Python 3.7 or higher
- FFmpeg
- v4l-utils (Video4Linux utilities)
- alsa-utils (for audio support)

## Installation

1. Clone this repository to your Raspberry Pi:
   ```
   git clone https://github.com/yourusername/rpi-usb-webcam.git
   cd rpi-usb-webcam
   ```

2. Create a virtual environment and install dependencies:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install flask flask-httpauth psutil
   ```

3. Install required system packages:
   ```
   sudo apt-get update
   sudo apt-get install -y ffmpeg v4l-utils
   ```

4. Set up as a service:
   ```
   sudo cp cam_server.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable cam_server
   sudo systemctl start cam_server
   ```

## Configuration

On first run, a default `config.ini` file will be created. You can edit this file to change the default settings.

Important configuration options:
- `username` and `password_hash` for authentication (generate with `python app_v5.py --set-password` or set `WEBCAM_AUTH_PASSWORD`)
- Video device path and resolution
- Audio settings
- Snapshot intervals
- Data storage locations

You can also configure all settings through the web interface after logging in.

Legacy application revisions are retained under `archive/` for reference.

## Docker

Build the container image:
```
docker build -t rpi-usb-webcam .
```

Run the container, supplying admin credentials and mapping the required devices/volumes as needed:
```
docker run --rm \
  --name rpi-usb-webcam \
  --device /dev/video0 \
  -e WEBCAM_AUTH_PASSWORD=yourStrongPassword \
  -p 8088:8088 \
  rpi-usb-webcam
```

Mount persistent storage for HLS segments and snapshots if desired:
```
docker run --rm \
  --name rpi-usb-webcam \
  --device /dev/video0 \
  -e WEBCAM_AUTH_PASSWORD=yourStrongPassword \
  -p 8088:8088 \
  -v ./static:/app/static \
  rpi-usb-webcam
```

## Usage

1. Set an admin password before first use:
   ```
   python app_v5.py --set-password
   ```
   (or provide `WEBCAM_AUTH_PASSWORD` when starting the server)

2. Access the web interface by opening a browser and navigating to:
   ```
   http://<raspberry-pi-ip>:8088
   ```

3. Log in with your username and password.

4. Use the web interface to:
   - View the live stream
   - Browse captured snapshots
   - Generate and view timelapse videos
   - Configure all settings

## Web Interface Features

The web interface provides:

- **Live Stream View**: Real-time video with audio (if available)
- **Snapshot Browser**: Browse snapshots organized by date with different viewing intervals (hourly, all, sample)
- **Timelapse Generator**: Create timelapse videos from any date's snapshots
- **Settings Panel**: Configure video/audio devices, resolution, snapshot intervals, authentication, and more
- **System Monitor**: View CPU, memory, disk usage, temperature, and uptime statistics
- **Device Detection**: Automatically detect connected webcams and audio devices

## Troubleshooting

### Stream not starting
- Verify the webcam is connected and detected: `ls -l /dev/video*`
- Check FFmpeg is installed: `ffmpeg -version`
- Review logs: `tail -f webcam_server.log`
- Ensure the video device path in config.ini matches your webcam

### Audio issues
- List available audio devices: `arecord -l`
- Update the audio device setting in the web interface or config.ini
- Disable audio in settings if your webcam doesn't have a microphone

### Permission denied errors
- Add your user to the video and audio groups:
  ```
  sudo usermod -a -G video,audio $USER
  ```
- Reboot or log out and back in for group changes to take effect

### High CPU usage
- Reduce video resolution in settings (e.g., 640x480 instead of 1920x1080)
- Increase HLS segment time
- Use a faster encoding preset (ultrafast is default)

### Cannot access web interface
- Check the server is running: `systemctl status cam_server` (if using systemd)
- Verify the port is not blocked by firewall
- Try accessing from the Raspberry Pi itself: `http://localhost:8088`

## API Endpoints

The application provides RESTful API endpoints (all require authentication):

- `GET /api/system` - Get system information and statistics
- `GET /api/devices` - List available webcams and audio devices
- `GET /api/resolutions?device=/dev/video0` - Get supported resolutions for a device
- `GET /api/settings` - Get current configuration
- `POST /api/settings` - Update configuration
- `POST /api/stream_control` - Control stream (actions: start, stop, restart)
- `GET /snapshots` - List today's snapshots
- `GET /snapshots/<date>` - List snapshots for specific date
- `GET /snapshot_dates` - List dates with available snapshots
- `GET /timelapses` - List available timelapse videos
- `POST /api/generate_timelapse` - Generate timelapse for a date

## Environment Variables

Configure authentication via environment variables:

- `WEBCAM_AUTH_USERNAME` - Override admin username (default: admin)
- `WEBCAM_AUTH_PASSWORD` - Set admin password (hashed automatically at runtime)
- `WEBCAM_AUTH_PASSWORD_HASH` - Use pre-hashed password (Werkzeug format)

Example:
```bash
export WEBCAM_AUTH_PASSWORD=mySecurePassword123
python app_v5.py
```

## Configuration File

The `config.ini` file supports comprehensive configuration:

```ini
[general]
port = 8088
host = 0.0.0.0
snapshot_interval = 30  # seconds between snapshots
max_days_to_keep = 7    # days to retain snapshots

[video]
device = /dev/video0
resolution = 1280x720
framerate = 30
preset = ultrafast      # FFmpeg encoding preset
hls_time = 1           # HLS segment duration
hls_list_size = 5      # Number of segments in playlist

[audio]
enabled = True
device = hw:1,0
sample_rate = 16000
bit_rate = 96k

[storage]
snapshot_dir = static/snapshots
hls_dir = static/hls
timelapse_dir = static/timelapse

[auth]
username = admin
password_hash =  # Use --set-password to configure
```

## Performance Tips

- **Lower resolution**: Use 640x480 or 800x600 for Raspberry Pi 3 or lower
- **Disable audio**: If not needed, disable audio capture to save CPU cycles
- **Adjust snapshot interval**: Increase interval to reduce disk I/O
- **Use Raspberry Pi 4**: For 1080p streaming, Pi 4 with 2GB+ RAM is recommended
- **Ethernet connection**: Use wired network for more reliable streaming

## Project Structure

```
rpi-usb-webcam/
├── app_v5.py              # Main application
├── requirements.txt       # Python dependencies
├── config.ini.sample      # Sample configuration
├── Dockerfile            # Docker container definition
├── cam_server.service    # Systemd service file
├── templates/            # HTML templates
│   └── index.html       # Web interface
├── archive/             # Legacy application versions
└── static/              # Generated at runtime
    ├── hls/            # HLS stream segments
    ├── snapshots/      # Captured snapshots (organized by date)
    └── timelapse/      # Generated timelapse videos
```

## Contributing

Contributions are welcome! Here's how you can help:

1. **Report bugs**: Open an issue describing the problem and steps to reproduce
2. **Suggest features**: Open an issue with your feature request
3. **Submit pull requests**: 
   - Fork the repository
   - Create a feature branch (`git checkout -b feature/amazing-feature`)
   - Commit your changes (`git commit -m 'Add amazing feature'`)
   - Push to the branch (`git push origin feature/amazing-feature`)
   - Open a Pull Request

Please ensure your code follows the existing style and includes appropriate documentation.

## Use Cases

This project is suitable for various applications:

- **Home Security**: Monitor your home while away with live streaming and snapshots
- **Wildlife Observation**: Set up a camera to observe wildlife with timelapse capabilities
- **Baby Monitor**: Keep an eye on your baby with audio and video streaming
- **Workshop/Garage Monitoring**: Monitor your workspace remotely
- **Weather Station**: Capture weather conditions with periodic snapshots
- **Construction Progress**: Document construction or renovation progress with timelapses
- **Garden Monitoring**: Track plant growth and garden conditions over time

## Security Considerations

- **Change default credentials**: Always set a strong password using `--set-password`
- **Network isolation**: Consider running on a separate VLAN or behind a firewall
- **HTTPS**: For internet-facing deployments, use a reverse proxy (nginx, Caddy) with SSL/TLS
- **Regular updates**: Keep Raspberry Pi OS and dependencies updated
- **Access control**: Use strong passwords and consider additional authentication layers
- **Port forwarding**: Be cautious when exposing the service to the internet

Example nginx reverse proxy configuration:
```nginx
server {
    listen 443 ssl;
    server_name webcam.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Version History

- **v5.0.0 (Current)**: Enhanced security, environment variable support, improved Docker support
- **v4.0.0**: Added configuration UI, system monitoring, improved UI, auto-detection of devices
- **v3.0.0**: Added higher resolution support and improved streaming performance
- **v2.0.0**: Added auto-restart capability for streaming and device detection
- **v1.0.0**: Initial release with basic streaming and snapshot features

## Acknowledgments

- Built with [Flask](https://flask.palletsprojects.com/) web framework
- Video processing powered by [FFmpeg](https://ffmpeg.org/)
- Uses [HLS (HTTP Live Streaming)](https://developer.apple.com/streaming/) protocol
- Inspired by the need for simple, reliable webcam streaming on Raspberry Pi

## Support

If you encounter issues or have questions:

1. Check the [Troubleshooting](#troubleshooting) section
2. Review existing [GitHub Issues](https://github.com/carteakey/rpi-usb-webcam/issues)
3. Open a new issue with detailed information about your problem

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
