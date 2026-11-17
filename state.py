import os
import queue
import threading
import numpy as np
from multiprocessing import shared_memory, Event
import atexit
from collections import deque

# Camera params
camH = 480
camW = 800
camFPS = 30

# Region of interest
roi_x, roi_y, roi_w, roi_h = 0, 0, 800, 480
line_pos = roi_h // 2

# Queues for thread communication
in_queue = queue.Queue(maxsize=1)
out_queue = queue.Queue(maxsize=1)
inspection_queue = queue.Queue()
out_img_queue = queue.Queue(maxsize=1)

# Shared memory for camera frames
shMem = shared_memory.SharedMemory(create=True, size=camH * camW * 3)
global_sh_Buf = np.ndarray((camH, camW, 3), dtype=np.uint8, buffer=shMem.buf)
shName = shMem.name
frameReady = Event()


def _cleanup_shared_memory():
    try:
        shMem.close()
    except Exception:
        pass
    try:
        shMem.unlink()
    except Exception:
        pass

atexit.register(_cleanup_shared_memory)

# Workflow state
app_state = "idle"  # idle, detect, record, exit
selected_mode = None
countdown_value = 55
relay_active = False
status_message = ""
mode_event = threading.Event()

# Bottle counting state
bottleCounter = 0
bottleCounter_2 = 0
# sentinel to avoid accidental immediate match at startup
badBottle = -999
pending_bad_count = 0
pending_bad_lock = threading.Lock()
relay_off_ts = 0.0
last_bad_ml_ts = 0.0
inspection_window_seconds = 0.65

# Live vision calibration and display state
vision_lock = threading.Lock()
vision_settings = {
    "enable_label_edge_check": True,
    "min_label_gx": 22.0,
    "min_label_gx_gy_ratio": 0.35,
    "min_purple_ratio": 0.35,
}
vision_metrics = {
    "invoke_ms": 0.0,
    "window_inf_count": 0,
    "mean_gx": 0.0,
    "mean_gy": 0.0,
    "ratio": 0.0,
    "purple_ratio": 0.0,
}
vision_gallery = deque(maxlen=4)
vision_gallery_version = 0
capture_test_requested = False
maintenance_minimize_requested = False
maintenance_minimized = False

# ML visual flag (timestamp of last bad detection)
ml_bad_ts = 0.0

# Recording UI progress
record_stage = "idle"
record_progress = 0.0
record_message = ""

# Picamera2/libcamera controls
camera_control_lock = threading.RLock()
active_camera = None
camera_settings = {
    "auto_exposure": True,
    "exposure_time_us": 4000,
    "analogue_gain": 6.0,
}

# Detection helpers
border = 150
distanceTraveledThreshold = 30

items = [
    [-1, -1, -1, -1, -1, 0, 0],
    [-1, -1, -1, -1, -1, 0, 0],
    [-1, -1, -1, -1, -1, 0, 0],
    [-1, -1, -1, -1, -1, 0, 0],
    [-1, -1, -1, -1, -1, 0, 0],
]

defaultArray = [
    [-1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0],
    [-1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0],
    [-1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0],
    [-1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0],
    [-1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0],
]