import argparse
import cv2
import importlib.util
import numpy as np
import os
import time
import queue
import threading
from collections import deque
from picamera2 import Picamera2
from gpiozero import LED, Button
import state

ENABLE_LABEL_EDGE_CHECK = True
TFLITE_NUM_THREADS = 4
PURPLE_H_MIN = 120
PURPLE_H_MAX = 160
PURPLE_S_MIN = 40
MIN_PURPLE_RATIO = 0.35
MIN_LABEL_GX = 22.0
MIN_LABEL_GX_GY_RATIO = 0.35
DEBUG_VISION_METRICS = True

pinRelay = LED(22)


def set_camera_settings(auto_exposure=None, exposure_time_us=None, analogue_gain=None):
    with state.camera_control_lock:
        updated_settings = dict(state.camera_settings)
        if auto_exposure is not None:
            updated_settings["auto_exposure"] = bool(auto_exposure)
        if exposure_time_us is not None:
            exposure_time_us = int(exposure_time_us)
            if not 100 <= exposure_time_us <= 30000:
                raise ValueError("Exposición fuera del rango permitido (100–30000 µs)")
            updated_settings["exposure_time_us"] = exposure_time_us
        if analogue_gain is not None:
            analogue_gain = float(analogue_gain)
            if not 1.0 <= analogue_gain <= 16.0:
                raise ValueError("Ganancia fuera del rango permitido (1–16)")
            updated_settings["analogue_gain"] = analogue_gain

        if state.active_camera is not None:
            state.active_camera.set_controls({
                "AeEnable": updated_settings["auto_exposure"],
                "ExposureTime": updated_settings["exposure_time_us"],
                "AnalogueGain": updated_settings["analogue_gain"],
            })
        state.camera_settings.update(updated_settings)


def _verify_printed_label(
    frame,
    xmin,
    ymin,
    xmax,
    ymax,
    min_label_gx=MIN_LABEL_GX,
    min_label_gx_gy_ratio=MIN_LABEL_GX_GY_RATIO,
    min_purple_ratio=MIN_PURPLE_RATIO,
):
    frame_height, frame_width = frame.shape[:2]
    xmin = max(0, min(frame_width, xmin))
    xmax = max(0, min(frame_width, xmax))
    ymin = max(0, min(frame_height, ymin))
    ymax = max(0, min(frame_height, ymax))
    bottle = frame[ymin:ymax, xmin:xmax]

    if bottle.shape[1] < 20 or bottle.shape[0] < 20:
        return True, 0.0, 0.0, 0.0, 0.0

    trim_y = int(bottle.shape[0] * 0.2)
    trim_x = int(bottle.shape[1] * 0.1)
    central_crop = bottle[
        trim_y:bottle.shape[0] - trim_y,
        trim_x:bottle.shape[1] - trim_x
    ]
    hsv = cv2.cvtColor(central_crop, cv2.COLOR_BGR2HSV)
    purple_mask = (
        (hsv[:, :, 0] >= PURPLE_H_MIN)
        & (hsv[:, :, 0] <= PURPLE_H_MAX)
        & (hsv[:, :, 1] >= PURPLE_S_MIN)
    )
    purple_ratio = float(np.mean(purple_mask))

    if purple_ratio < min_purple_ratio:
        return True, 0.0, 0.0, 0.0, purple_ratio

    gray = cv2.cvtColor(central_crop, cv2.COLOR_BGR2GRAY)
    normalized = cv2.resize(gray, (128, 128))
    grad_x = cv2.Sobel(normalized, cv2.CV_64F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(normalized, cv2.CV_64F, 0, 1, ksize=3)
    mean_gx = float(np.mean(np.abs(grad_x)))
    mean_gy = float(np.mean(np.abs(grad_y)))
    ratio = mean_gx / (mean_gy + 1e-6)
    passed = mean_gx >= min_label_gx and ratio >= min_label_gx_gy_ratio
    return passed, mean_gx, mean_gy, ratio, purple_ratio


def sortItemsArray(items):
    distance = 640 * 3
    smallestDistance = 640 * 3
    smallestDistanceID = -1
    itemsSorted = [[-1, -1, -1, -1, -1, 0, 0] for _ in range(5)]

    for i in range(len(items)):
        for j in range(len(items)):
            if items[i][0] != -1 and items[i][2] != -1:
                distance = np.sqrt((items[i][0] - items[j][2])**2 + (items[i][1] - items[j][3])**2)
            if distance < smallestDistance:
                smallestDistance = distance
                smallestDistanceID = j

        if smallestDistanceID == -1:
            for j in range(len(itemsSorted)):
                if itemsSorted[j][0] == -1:
                    smallestDistanceID = j
                    break

        itemsSorted[smallestDistanceID] = items[i]
        distance = 640 * 3
        smallestDistance = 640 * 3
        smallestDistanceID = -1

    return itemsSorted


def trackBottle(items):
    for i in range(len(items)):
        if items[i][0] != -1 or items[i][2] != -1:
            if items[i][0] == items[i][2]:
                items[i][0] -= 0
                items[i][4] += 1
            else:
                items[i][4] = 0

            if abs(items[i][0] - items[i][2]) < 50 and items[i][4] < 5:
                items[i][6] += abs(items[i][0] - items[i][2])
                if (items[i][0] < state.border and not items[i][5] and items[i][4] < 3 and items[i][6] > state.distanceTraveledThreshold):
                    state.bottleCounter += 1
                    items[i][5] = 1

            items[i][2] = items[i][0]
            items[i][3] = items[i][1]

            if items[i][0] < 40 or ((items[i][4] > 4) and items[i][0] < 150):
                items[i] = [-1, -1, -1, -1, -1, 0, 0]


def camera_thread():
    picam2 = Picamera2()
    camera_started = False
    try:
        config = picam2.create_preview_configuration(main={"format": "RGB888", "size": (state.camW, state.camH)}, controls={'FrameRate': state.camFPS})
        picam2.configure(config)
        with state.camera_control_lock:
            camera_settings = dict(state.camera_settings)
            picam2.set_controls({
                "AeEnable": camera_settings["auto_exposure"],
                "ExposureTime": camera_settings["exposure_time_us"],
                "AnalogueGain": camera_settings["analogue_gain"],
            })
            state.active_camera = picam2
        picam2.start()
        camera_started = True
        time.sleep(2)

        cam_shMem = state.shared_memory.SharedMemory(name=state.shName)
        sh_buf = np.ndarray((state.camH, state.camW, 3), dtype=np.uint8, buffer=cam_shMem.buf)

        while state.app_state != 'exit':
            model_frame = picam2.capture_array()
            frame_bgr = cv2.cvtColor(model_frame, cv2.COLOR_RGB2BGR)
            if not state.in_queue.full():
                state.in_queue.put((model_frame, frame_bgr, time.monotonic()))
    finally:
        with state.camera_control_lock:
            if state.active_camera is picam2:
                state.active_camera = None
            if camera_started:
                picam2.stop()
            picam2.close()


def ml_thread():
    model_version_name = 'detect_v2.tflite'
    isLabeled = -1
    result = state.defaultArray.copy()
    active_inspections = deque()
    future_inspections = deque()
    ml_shMem = state.shared_memory.SharedMemory(name=state.shName)
    sh_buf = np.ndarray((state.camH, state.camW, 3), dtype=np.uint8, buffer=ml_shMem.buf)

    parser = argparse.ArgumentParser()
    parser.add_argument('--modeldir', default='/home/raspberry/Desktop/Trained_Model/custom_model_lite')
    parser.add_argument('--graph', default=model_version_name)
    parser.add_argument('--labels', default='labelmap.txt')
    parser.add_argument('--threshold', default=0.82)
    parser.add_argument('--resolution', default='800x480')
    parser.add_argument('--edgetpu', action='store_true')
    args = parser.parse_args([])

    MODEL_NAME = args.modeldir
    GRAPH_NAME = args.graph
    LABELMAP_NAME = args.labels
    min_conf_threshold = float(args.threshold)
    resW, resH = args.resolution.split('x')
    imW, imH = int(resW), int(resH)
    use_TPU = args.edgetpu

    pkg = importlib.util.find_spec('tflite_runtime')
    if pkg:
        from tflite_runtime.interpreter import Interpreter
        if use_TPU:
            from tflite_runtime.interpreter import load_delegate
    else:
        from tensorflow.lite.python.interpreter import Interpreter
        if use_TPU:
            from tensorflow.lite.python.interpreter import load_delegate

    if use_TPU and GRAPH_NAME == 'detect.tflite':
        GRAPH_NAME = 'edgetpu.tflite'

    CWD_PATH = os.getcwd()
    PATH_TO_CKPT = os.path.join(CWD_PATH, MODEL_NAME, GRAPH_NAME)
    PATH_TO_LABELS = os.path.join(CWD_PATH, MODEL_NAME, LABELMAP_NAME)

    with open(PATH_TO_LABELS, 'r') as f:
        labels = [line.strip() for line in f.readlines()]
    if labels[0] == '???':
        del(labels[0])

    if use_TPU:
        interpreter = Interpreter(model_path=PATH_TO_CKPT,
                                  experimental_delegates=[load_delegate('libedgetpu.so.1.0')],
                                  num_threads=TFLITE_NUM_THREADS)
    else:
        interpreter = Interpreter(model_path=PATH_TO_CKPT, num_threads=TFLITE_NUM_THREADS)
    interpreter.allocate_tensors()

    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    height = input_details[0]['shape'][1]
    width = input_details[0]['shape'][2]
    floating_model = (input_details[0]['dtype'] == np.float32)

    outname = output_details[0]['name']
    if 'StatefulPartitionedCall' in outname:
        boxes_idx, classes_idx, scores_idx = 1, 3, 0
    else:
        boxes_idx, classes_idx, scores_idx = 0, 1, 2

    while state.app_state != 'exit':
        try:
            frameReady = state.frameReady
            frameReady.clear()
            isLabeled = -1
            tStart = time.time()
            model_frame, frame_bgr, frame_timestamp = state.in_queue.get_nowait()
            model_frame = model_frame.copy()
            frame_bgr = frame_bgr.copy()

            while future_inspections and future_inspections[0][1] <= frame_timestamp:
                active_inspections.append([*future_inspections.popleft(), False, 0])
            while True:
                try:
                    inspection = state.inspection_queue.get_nowait()
                except queue.Empty:
                    break
                if inspection[1] <= frame_timestamp:
                    active_inspections.append([*inspection, False, 0])
                else:
                    future_inspections.append(inspection)
                    break

            roiFrame = model_frame[state.roi_y:state.roi_y + state.roi_h, state.roi_x:state.roi_x + state.roi_w]
            roiFrame_bgr = frame_bgr[state.roi_y:state.roi_y + state.roi_h, state.roi_x:state.roi_x + state.roi_w]
            frame_resized = cv2.resize(roiFrame, (width, height))
            input_data = np.expand_dims(frame_resized, axis=0)

            if floating_model:
                input_data = (np.float32(input_data) - 127.5) / 127.5

            interpreter.set_tensor(input_details[0]['index'], input_data)
            invoke_start = time.perf_counter()
            interpreter.invoke()
            invoke_ms = (time.perf_counter() - invoke_start) * 1000
            inference_timestamp = time.monotonic()
            window_inf_count = 0
            for inspection in active_inspections:
                started_at = inspection[1]
                if started_at <= inference_timestamp <= started_at + state.inspection_window_seconds:
                    inspection[3] += 1
                    window_inf_count = max(window_inf_count, inspection[3])

            boxes = interpreter.get_tensor(output_details[boxes_idx]['index'])[0]
            classes = interpreter.get_tensor(output_details[classes_idx]['index'])[0]
            scores = interpreter.get_tensor(output_details[scores_idx]['index'])[0]

            result = state.defaultArray.copy()
            labeled_found = False
            display_detections = []
            for i in range(5):
                if (scores[i] >= min_conf_threshold) and (scores[i] <= 1.0):
                    ymin = int(max(1, (boxes[i][0] * imH)))
                    xmin = int(max(1, (boxes[i][1] * imW)))
                    ymax = int(min(imH, (boxes[i][2] * imH)))
                    xmax = int(min(imW, (boxes[i][3] * imW)))

                    detected_label = labels[int(classes[i])]
                    raw_label = detected_label
                    metrics = (0.0, 0.0, 0.0, 0.0)
                    if raw_label == 'labeled':
                        with state.vision_lock:
                            vision_settings = dict(state.vision_settings)
                        if vision_settings["enable_label_edge_check"]:
                            passed, mean_gx, mean_gy, ratio, purple_ratio = _verify_printed_label(
                                roiFrame_bgr,
                                xmin,
                                ymin,
                                xmax,
                                ymax,
                                vision_settings["min_label_gx"],
                                vision_settings["min_label_gx_gy_ratio"],
                                vision_settings["min_purple_ratio"],
                            )
                            metrics = (mean_gx, mean_gy, ratio, purple_ratio)
                            if not passed:
                                detected_label = 'NO_labeled'
                        else:
                            passed = True

                    if raw_label == 'labeled' and DEBUG_VISION_METRICS:
                        mean_gx, mean_gy, ratio, purple_ratio = metrics
                        print(
                            f'[VISION_DEBUG] invoke={invoke_ms:.1f}ms | '
                            f'inf_win={window_inf_count} | purple={purple_ratio:.2f} | '
                            f'Gx={mean_gx:.1f} Gy={mean_gy:.1f} ratio={ratio:.3f} | '
                            f'raw=labeled -> {detected_label}'
                        )

                    if detected_label == 'labeled':
                        isLabeled = 1
                        labeled_found = True
                    elif detected_label == 'NO_labeled':
                        isLabeled = 0
                    else:
                        isLabeled = -1

                    if detected_label == 'labeled':
                        decision = 'accepted'
                    elif raw_label == 'labeled':
                        decision = 'corrected'
                    else:
                        decision = 'direct_reject'
                    mean_gx, mean_gy, ratio, purple_ratio = metrics
                    display_detections.append({
                        "box": (xmin, ymin, xmax, ymax),
                        "raw_label": raw_label,
                        "label": detected_label,
                        "decision": decision,
                        "mean_gx": mean_gx,
                        "mean_gy": mean_gy,
                        "ratio": ratio,
                        "purple_ratio": purple_ratio,
                    })

                    label = f'{detected_label}: {int(scores[i]*100)}%'
                    labelSize, baseLine = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                    label_ymin = max(ymin, labelSize[1] + 10)

                    result[i][0] = isLabeled
                    result[i][1] = xmin
                    result[i][2] = xmax
                    result[i][3] = ymin
                    result[i][4] = ymax
                    result[i][5] = label_ymin
                    result[i][6] = labelSize[0]
                    result[i][7] = labelSize[1]
                    result[i][8] = baseLine
                else:
                    result[i][0] = -1
                    break

            with state.vision_lock:
                state.vision_metrics.update({
                    "invoke_ms": invoke_ms,
                    "window_inf_count": window_inf_count,
                    "mean_gx": display_detections[-1]["mean_gx"] if display_detections else 0.0,
                    "mean_gy": display_detections[-1]["mean_gy"] if display_detections else 0.0,
                    "ratio": display_detections[-1]["ratio"] if display_detections else 0.0,
                    "purple_ratio": display_detections[-1]["purple_ratio"] if display_detections else 0.0,
                })
            result[0][11] = {
                "invoke_ms": invoke_ms,
                "window_inf_count": window_inf_count,
                "detections": display_detections,
            }

            if labeled_found:
                for inspection in active_inspections:
                    bottle_id, started_at, confirmed, _ = inspection
                    if started_at <= frame_timestamp <= started_at + state.inspection_window_seconds:
                        inspection[2] = True
                        break

            while (
                active_inspections
                and frame_timestamp > active_inspections[0][1] + state.inspection_window_seconds
            ):
                bottle_id, started_at, confirmed, _ = active_inspections.popleft()
                if not confirmed:
                    state.badBottle = bottle_id
                    with state.pending_bad_lock:
                        state.pending_bad_count += 1

            tEnd = time.time()
            fps = 1 / (tEnd - tStart)
            result[0][9] = fps
            result[0][10] = tEnd

            if state.out_queue.full():
                try:
                    state.out_queue.get_nowait()
                except queue.Empty:
                    pass
            state.out_queue.put(result)

            if state.out_img_queue.full():
                try:
                    state.out_img_queue.get_nowait()
                except queue.Empty:
                    pass
            state.out_img_queue.put(frame_bgr)

            state.frameReady.set()
        except queue.Empty:
            pass


def gpio_thread():
    while state.app_state != 'exit':
        if state.badBottle == -1:
            pinRelay.on()
            state.badBottle = 0
            print('Pin has sido activado...')
            time.sleep(2.5)
            pinRelay.off()
            with state.pending_bad_lock:
                state.pending_bad_count = max(0, state.pending_bad_count - 1)
        time.sleep(0.1)


def bottleDetected():
    state.bottleCounter += 1
    state.inspection_queue.put((state.bottleCounter, time.monotonic()))
    print(f'Bottle counter: {state.bottleCounter}')


def bottleDetected_2():
    state.bottleCounter_2 += 1
    print(f'Bottle counter 2: {state.bottleCounter_2}')


def gpio_counter():
    pinSensor_1 = Button(17, pull_up=True, bounce_time=0.08)
    pinSensor_1.when_activated = bottleDetected

    while state.app_state != 'exit':
        time.sleep(0.1)


def gpio_counter_2():
    pinSensor_2 = Button(27, pull_up=True, bounce_time=0.08)
    pinSensor_2.when_activated = bottleDetected_2

    while state.app_state != 'exit':
        if state.badBottle == state.bottleCounter_2:
            state.badBottle = -1
        time.sleep(0.1)


def display_loop():
    winName = 'Detector de botellas'
    cv2.namedWindow(winName, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(winName, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    def request_maintenance_minimize(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDBLCLK and x < 100 and y < 100:
            with state.vision_lock:
                state.maintenance_minimize_requested = True

    cv2.setMouseCallback(winName, request_maintenance_minimize)
    window_minimized = False
    last_rejection_capture = 0.0
    stop_file = os.path.join(os.getcwd(), 'stop_detector')
    while state.app_state == 'detect':
        if os.path.exists(stop_file):
            state.status_message = 'Salida por archivo stop_detector'
            state.app_state = 'exit'
            break
        if state.frameReady.wait(timeout=1):
            try:
                result = state.out_queue.get_nowait()
                processed_frame = state.out_img_queue.get_nowait()
            except queue.Empty:
                continue
            metadata = result[0][11] if len(result[0]) > 11 and isinstance(result[0][11], dict) else {}
            for detection in metadata.get("detections", []):
                xmin, ymin, xmax, ymax = detection["box"]
                if detection["decision"] == "accepted":
                    color = (0, 220, 100)
                elif detection["decision"] == "corrected":
                    color = (0, 145, 255)
                else:
                    color = (40, 40, 240)
                trim_y = int((ymax - ymin) * 0.2)
                trim_x = int((xmax - xmin) * 0.1)
                inner_box = (
                    xmin + trim_x,
                    ymin + trim_y,
                    xmax - trim_x,
                    ymax - trim_y,
                )
                cv2.rectangle(processed_frame, (xmin, ymin), (xmax, ymax), color, 2)
                cv2.rectangle(processed_frame, inner_box[:2], inner_box[2:], color, 2)
                metric_text = (
                    f"Gx {detection['mean_gx']:.1f}  "
                    f"r {detection['ratio']:.3f}  "
                    f"purple {detection['purple_ratio']:.2f}"
                )
                cv2.putText(
                    processed_frame,
                    metric_text,
                    (xmin, max(20, ymin - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.48,
                    color,
                    1,
                    cv2.LINE_AA,
                )

            if result[0][0] != -1:
                for i in range(len(result)):
                    if result[i][0] == 1:
                        label_text = 'Labeled'
                    elif result[i][0] == 0:
                        label_text = 'No Labeled'
                    else:
                        break
                    if i < len(metadata.get("detections", [])):
                        decision = metadata["detections"][i]["decision"]
                        if decision == "accepted":
                            box_color = (0, 220, 100)
                        elif decision == "corrected":
                            box_color = (0, 145, 255)
                        else:
                            box_color = (40, 40, 240)
                    else:
                        box_color = (0, 220, 100)
                    cv2.rectangle(processed_frame, (result[i][1], result[i][3]), (result[i][2], result[i][4]), box_color, 2)
                    cv2.rectangle(processed_frame, (result[i][1], result[i][5]-result[i][7]-10), (result[i][1]+result[i][6], result[i][5]+result[i][8]-10), (255, 255, 255), cv2.FILLED)
                    cv2.putText(processed_frame, f'{label_text} id: {i}', (result[i][1], result[i][5]-7), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)

            overlay = processed_frame.copy()
            cv2.rectangle(overlay, (0, 0), (state.camW, 58), (15, 23, 42), cv2.FILLED)
            cv2.addWeighted(overlay, 0.82, processed_frame, 0.18, 0, processed_frame)
            cv2.putText(
                processed_frame,
                f"DETECT  |  invoke {metadata.get('invoke_ms', 0.0):.1f} ms  |  "
                f"inf/ventana {metadata.get('window_inf_count', 0)}  |  "
                f"S1 {state.bottleCounter}  S2 {state.bottleCounter_2}",
                (18, 38),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (248, 250, 252),
                2,
                cv2.LINE_AA,
            )
            with state.pending_bad_lock:
                pending_bad_count = state.pending_bad_count
            cv2.putText(
                processed_frame,
                f"PENDIENTES {pending_bad_count}   |   [M] minimizar",
                (18, state.camH - 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (248, 250, 252),
                2,
                cv2.LINE_AA,
            )

            now = time.monotonic()
            rejected = any(detection["decision"] != "accepted" for detection in metadata.get("detections", []))
            with state.vision_lock:
                capture_requested = state.capture_test_requested
                if capture_requested:
                    state.capture_test_requested = False
            if capture_requested or (rejected and now - last_rejection_capture >= state.inspection_window_seconds):
                success, encoded = cv2.imencode(".png", processed_frame)
                if success:
                    with state.vision_lock:
                        state.vision_gallery.append({
                            "kind": "prueba" if capture_requested else "rechazo",
                            "timestamp": time.strftime("%H:%M:%S"),
                            "image": encoded.tobytes(),
                        })
                        state.vision_gallery_version += 1
                if rejected:
                    last_rejection_capture = now

            with state.vision_lock:
                maintenance_minimized = state.maintenance_minimized
            if maintenance_minimized and not window_minimized:
                cv2.setWindowProperty(winName, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(winName, 320, 240)
                window_minimized = True
            elif not maintenance_minimized and window_minimized:
                cv2.setWindowProperty(winName, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                window_minimized = False

            display_size = (320, 240) if window_minimized else (state.camW, state.camH)
            resizedFrame = cv2.resize(processed_frame, display_size, interpolation=cv2.INTER_LINEAR)
            cv2.imshow(winName, resizedFrame)

        key = cv2.waitKeyEx(1)
        key_code = key & 0xFF
        if key_code == ord('q') or key_code == 27:
            state.app_state = 'exit'
            break
        elif key_code in (ord('m'), ord('M')) or key in (123, 0x7B0000, 0xFFC9, 0x0100003B):
            with state.vision_lock:
                state.maintenance_minimize_requested = True
        elif key_code == ord('f'):
            cv2.setWindowProperty(winName, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)

    cv2.destroyAllWindows()
    state.app_state = 'exit'


def start_detection():
    state.status_message = 'Modo DETECT activado'

    thread1 = threading.Thread(target=camera_thread, daemon=True)
    thread2 = threading.Thread(target=ml_thread, daemon=True)
    thread3 = threading.Thread(target=gpio_thread, daemon=True)
    thread4 = threading.Thread(target=gpio_counter, daemon=True)
    thread5 = threading.Thread(target=gpio_counter_2, daemon=True)
    thread1.start()
    thread2.start()
    thread3.start()
    thread4.start()
    thread5.start()

    display_thread = threading.Thread(target=display_loop, daemon=True)
    display_thread.start()
