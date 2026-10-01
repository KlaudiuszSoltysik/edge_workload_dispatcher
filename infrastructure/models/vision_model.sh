#!/bin/bash
input_source="${1:-default_camera_feed}"
echo "[Vision Model] Analyzing video stream from: $input_source"
sleep 1
echo "Detections: [{\"class\": \"vehicle\", \"confidence\": 0.98, \"bbox\": [120, 45, 200, 110]}]"