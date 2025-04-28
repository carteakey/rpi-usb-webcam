sudo cp cam_server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cam_server
sudo systemctl start cam_server
# rpi-usb-webcam
