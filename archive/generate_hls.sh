#!/bin/bash

mkdir -p static/hls

ffmpeg -f v4l2 -i /dev/video0 \
       -f alsa -i default \
       -c:v libx264 -preset ultrafast -tune zerolatency \
       -c:a aac -b:a 128k \
       -f hls -hls_time 2 -hls_list_size 5 -hls_flags delete_segments+append_list \
       static/hls/stream.m3u8
