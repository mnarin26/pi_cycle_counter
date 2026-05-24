import os
import sys
import time

os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;200000|reorder_queue_size;0",
)
import cv2  # noqa: E402

url = sys.argv[1] if len(sys.argv) > 1 else "rtsp://cam1_alfa:cam1_alfa@192.168.4.20:554/stream2"
cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
print("opened", cap.isOpened())
t0 = time.time()
ok, frame = False, None
for i in range(30):
    ok = cap.grab()
    if ok:
        ok, frame = cap.retrieve()
        if ok and frame is not None:
            print("frame", i, frame.shape, "dt", round(time.time() - t0, 2))
            break
    time.sleep(0.1)
print("final_ok", ok, "has_frame", frame is not None)
cap.release()
