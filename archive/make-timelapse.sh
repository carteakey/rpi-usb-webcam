#!/bin/bash

TODAY=$(date +%Y-%m-%d)
SNAPSHOT_DIR="/home/pi/services/rpi-usb-webcam/static/snapshots/$TODAY"
OUTPUT_DIR="/home/pi/services/rpi-usb-webcam/static/timelapse"

mkdir -p "$OUTPUT_DIR"

ffmpeg -pattern_type glob -i "$SNAPSHOT_DIR/*.jpg" -c:v libx264 -vf "fps=10,format=yuv420p" "$OUTPUT_DIR/${TODAY}_timelapse.mp4"
