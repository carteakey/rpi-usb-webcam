# Raspberry Pi USB Webcam Server

A simple and powerful web server for streaming USB webcam video with snapshots and timelapse features.

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
- `username` and `password` for authentication
- Video device path and resolution
- Audio settings
- Snapshot intervals
- Data storage locations

You can also configure all settings through the web interface after logging in.

## Usage

1. Access the web interface by opening a browser and navigating to:
   ```
   http://<raspberry-pi-ip>:8088
   ```

2. Log in with your username and password (default: admin/change_this_password)

3. Use the web interface to:
   - View the live stream
   - Browse captured snapshots
   - Generate and view timelapse videos
   - Configure all settings

## Version History

- v4.0.0: Added configuration UI, system monitoring, improved UI, auto-detection of devices
- v3.0.0: Added higher resolution support and improved streaming performance
- v2.0.0: Added auto-restart capability for streaming and device detection
- v1.0.0: Initial release with basic streaming and snapshot features

## License

This project is licensed under the MIT License - see the LICENSE file for details.