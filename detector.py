import argparse
import cv2
import importlib.util
import numpy as np
import os
import time
import queue
import threading
from picamera2 import Picamera2
from gpiozero import LED, Button
import state

pinRelay = LED(22)


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
    config = picam2.create_preview_configuration(main={"format": "RGB888", "size": (state.camW, state.camH)}, controls={'FrameRate': state.camFPS})
    picam2.configure(config)
    picam2.set_controls({"AeEnable": False, "ExposureTime": 4000})
    picam2.set_controls({"AnalogueGain": 6.0})
    picam2.start()
    time.sleep(2)

    cam_shMem = state.shared_memory.SharedMemory(name=state.shName)
    sh_buf = np.ndarray((state.camH, state.camW, 3), dtype=np.uint8, buffer=cam_shMem.buf)

    while state.app_state != 'exit':
        frame = picam2.capture_array()
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2RGBA)
        if not state.in_queue.full():
            state.in_queue.put(frame_bgr[:, :, :3])
    picam2.stop()
    picam2.close()


def ml_thread():
    model_version_name = 'detect_v2.tflite'
    cont = 0
    isLabeled = -1
    flag = 0
    result = state.defaultArray.copy()
    ml_shMem = state.shared_memory.SharedMemory(name=state.shName)
    sh_buf = np.ndarray((state.camH, state.camW, 3), dtype=np.uint8, buffer=ml_shMem.buf)

    parser = argparse.ArgumentParser()
    parser.add_argument('--modeldir', default='/home/raspberry/Desktop/Trained_Model/custom_model_lite')
    parser.add_argument('--graph', default=model_version_name)
    parser.add_argument('--labels', default='labelmap.txt')
    parser.add_argument('--threshold', default=0.87)
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
                                  num_threads=4)
    else:
        interpreter = Interpreter(model_path=PATH_TO_CKPT, num_threads=4)
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
            flag = 0
            isLabeled = -1
            tStart = time.time()
            frame = state.in_queue.get_nowait().copy()
            roiFrame = frame[state.roi_y:state.roi_y + state.roi_h, state.roi_x:state.roi_x + state.roi_w]
            frame_resized = cv2.resize(roiFrame, (width, height))
            input_data = np.expand_dims(frame_resized, axis=0)

            if floating_model:
                input_data = (np.float32(input_data) - 127.5) / 127.5

            interpreter.set_tensor(input_details[0]['index'], input_data)
            interpreter.invoke()

            boxes = interpreter.get_tensor(output_details[boxes_idx]['index'])[0]
            classes = interpreter.get_tensor(output_details[classes_idx]['index'])[0]
            scores = interpreter.get_tensor(output_details[scores_idx]['index'])[0]

            result = state.defaultArray.copy()
            for i in range(5):
                if (scores[i] > min_conf_threshold) and (scores[i] <= 1.0):
                    flag = 1
                    ymin = int(max(1, (boxes[i][0] * imH)))
                    xmin = int(max(1, (boxes[i][1] * imW)))
                    ymax = int(min(imH, (boxes[i][2] * imH)))
                    xmax = int(min(imW, (boxes[i][3] * imW)))

                    if labels[int(classes[i])] == 'labeled':
                        isLabeled = 1
                    elif labels[int(classes[i])] == 'NO_labeled':
                        cont = 3
                        if cont > 2:
                            cont = 0
                            isLabeled = 0
                            print('No labeled')
                    else:
                        isLabeled = -1

                    label = f'{labels[int(classes[i])]}: {int(scores[i]*100)}%'
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

            if state.flag_queue.full():
                try:
                    state.flag_queue.get_nowait()
                except queue.Empty:
                    pass
            state.flag_queue.put(flag)

            if state.out_img_queue.full():
                try:
                    state.out_img_queue.get_nowait()
                except queue.Empty:
                    pass
            state.out_img_queue.put(frame)

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
        time.sleep(0.1)


def bottleDetected():
    state.bottleCounter += 1
    print(f'Bottle counter: {state.bottleCounter}')


def bottleDetected_2():
    state.bottleCounter_2 += 1
    print(f'Bottle counter 2: {state.bottleCounter_2}')


def gpio_counter():
    pinSensor_1 = Button(17, pull_up=True)
    pinSensor_1.when_activated = bottleDetected

    while state.app_state != 'exit':
        try:
            flag = state.flag_queue.get(timeout=0.5)
            if flag:
                state.badBottle = state.bottleCounter
        except queue.Empty:
            pass


def gpio_counter_2():
    pinSensor_2 = Button(27, pull_up=True)
    pinSensor_2.when_activated = bottleDetected_2

    while state.app_state != 'exit':
        if state.badBottle == state.bottleCounter_2:
            state.badBottle = -1
        time.sleep(0.1)


def display_loop():
    winName = 'Detector de botellas'
    cv2.namedWindow(winName, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(winName, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

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

            if result[0][0] != -1:
                for i in range(len(result)):
                    if result[i][0] == 1:
                        label_text = 'Labeled'
                    elif result[i][0] == 0:
                        label_text = 'No Labeled'
                    else:
                        break
                    cv2.rectangle(processed_frame, (result[i][1], result[i][3]), (result[i][2], result[i][4]), (10, 255, 0), 2)
                    cv2.rectangle(processed_frame, (result[i][1], result[i][5]-result[i][7]-10), (result[i][1]+result[i][6], result[i][5]+result[i][8]-10), (255, 255, 255), cv2.FILLED)
                    cv2.putText(processed_frame, f'{label_text} id: {i}', (result[i][1], result[i][5]-7), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)

            cv2.rectangle(processed_frame, (state.roi_x, state.roi_y), (state.roi_x + state.roi_w, state.roi_y + state.roi_h), (10, 255, 0), 1)
            cv2.putText(processed_frame, f'FPS: {result[0][9]:.2f}', (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2, cv2.LINE_AA)
            cv2.putText(processed_frame, f'Botellas: {state.bottleCounter}', (30, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            cv2.putText(processed_frame, f'Botellas 2: {state.bottleCounter_2}', (30, 150), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            if state.relay_active:
                cv2.putText(processed_frame, 'Electrovalvula activada', (30, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            resizedFrame = cv2.resize(processed_frame, (state.camW, state.camH), interpolation=cv2.INTER_LINEAR)
            cv2.imshow(winName, resizedFrame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == 27:
            state.app_state = 'exit'
            break
        elif key == ord('f'):
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
