# Import packages
import os
import argparse
import cv2
import numpy as np
import sys
import time
import queue
import threading
import importlib.util
from picamera2 import Picamera2
from gpiozero import LED, Button
from multiprocessing import Process, Queue, shared_memory
import multiprocessing as mp

# Pin def
pinRelay = LED(22)
# pinSensor_1 = Button(17)
# pinSensor_2 = Button(27)

# Creating queues
in_queue = queue.Queue(maxsize=1)
out_queue = queue.Queue(maxsize=1)
# box_queue = queue.Queue(maxsize=1)
# classes_queue = queue.Queue(maxsize=1)
# score_queue = queue.Queue(maxsize=1)
flag_queue = queue.Queue(maxsize=1)
out_img_queue = queue.Queue(maxsize=1)

# Camera params
# H x W & fps
camH = 480      # 480
camW = 800      # 640
camFPS = 30

# Setting ROI
roi_x, roi_y, roi_w, roi_h = 0, 0, 800, 480
line_pos = roi_h // 2

# Global Variables
label = ""
framesToSkip = 0
frameCount = 0
shMem = shared_memory.SharedMemory(create=True, size=camH * camW * 3)   # size >= h x w x 3
global_sh_Buf = np.ndarray((camH, camW, 3), dtype=np.uint8, buffer=shMem.buf)
shName = shMem.name
frameReady = mp.Event()
# frameShown = mp.Event()
avgSpeedPixels = 0  #10
border = 150    # Border to count bottles when they cross it
distanceTraveledThreshold = 30
bottleCounter = 0
bottleCounter_2 = 0
badBottle = 0
items = [                       # items = [x, y, prevX, prevY, missedFrames, wasCounted, distanceTraveled]
    [-1,-1,-1,-1,-1,0,0],
    [-1,-1,-1,-1,-1,0,0],
    [-1,-1,-1,-1,-1,0,0],
    [-1,-1,-1,-1,-1,0,0],
    [-1,-1,-1,-1,-1,0,0]
]
defaultArray = [ # Replace copy with deep copy
    [-1,0,0,0,0,0,0,0,0,1,1,0],
    [-1,0,0,0,0,0,0,0,0,1,1,0],
    [-1,0,0,0,0,0,0,0,0,1,1,0],
    [-1,0,0,0,0,0,0,0,0,1,1,0],
    [-1,0,0,0,0,0,0,0,0,1,1,0]
]

# Functions

def sortItemsArray(items):
    # Local variables
    distance = 640 * 3
    smallestDistance = 640 * 3
    smallestDistanceID = -1
    itemsSorted = [
        [-1,-1,-1,-1,-1,0,0],
        [-1,-1,-1,-1,-1,0,0],
        [-1,-1,-1,-1,-1,0,0],
        [-1,-1,-1,-1,-1,0,0],
        [-1,-1,-1,-1,-1,0,0]
    ]
    # itemsCopy = items.copy()

    for i in range(len(items)):
        # Compute distance for current item vs all previous distances saved
        for j in range(len(items)):
            # Calculating distance, if values are not empty
            if (items[i][0] != -1 and items[i][2] != -1):
                distance = np.sqrt((items[i][0] - items[j][2])**2 + (items[i][1] - items[j][3])**2)
                # print("Distance calculated: " + str(distance))
            # Comparing against smallest distance
            if (distance < smallestDistance):
                smallestDistance = distance
                smallestDistanceID = j
        # After getting the smallest distance, copy the current pos into that slot:
        if(smallestDistanceID == -1): 
            # If item could not be sorted, add it in the next free slot
            for j in range (len(itemsSorted)):
                if (itemsSorted[j][0] == -1):
                    smallestDistanceID = j
                    break
        # Saving values
        # print("Smallest distance ID: " + str(smallestDistanceID))
        itemsSorted[smallestDistanceID] = items[i]
        # print("Saved item " + str(i) + " in pos: " + str(smallestDistanceID))
        #resetting comparision variables
        distance = 640 * 3
        smallestDistance = 640 * 3
        smallestDistanceID = -1

    return itemsSorted

def trackBottle(items):
    global bottleCounter
    for i in range(len(items)):
        # print("Item's id: " + str(i))
        # print("Current x pos: " + str(items[i][0]))
        # print("Previous x pos: " + str(items[i][2]))
        if(items[i][0] != -1 or items[i][2] != -1):
            # Increase the predicted position in x axis, conveyor runs right to left, if the previous and the current pos are the same
            if(items[i][0] == items[i][2]):
                # print("Increased prediction pos and counter... ID: " + str(i))
                items[i][0] -= avgSpeedPixels # x pixels to the left
                # Increase frames missed counter
                items[i][4] += 1
            else:
                # Reset the missed frames counter
                # print("Items frames reset for id: " + str(i))
                items[i][4] = 0

            # Check if item's center pos is still inside the bounds, and is still in the picture
            if (abs(items[i][0] - items[i][2]) < 50) and (items[i][4] < 5):
                # Adding distance traveled
                items[i][6] += abs(items[i][0] - items[i][2])
                # Check if border was crossed, item wasn't count, and if the frames skipped are less than 3, and if the distance traveled is more than 70px
                if (items[i][0] < border) and (not items[i][5]) and (items[i][4] < 3) and (items[i][6] > distanceTraveledThreshold):
                    # Counts the bottle
                    bottleCounter += 1
                    items[i][5] = 1

            # Update prev pos
            items[i][2] = items[i][0]
            items[i][3] = items[i][1]
            
            # If bottle's position in x is under 40px or missing for over 5 frames, while on the lower limit < 150, delete it from the array
            if(items[i][0] < 40) or ((items[i][4] > 4) and items[i][0] < 150):
                # Resets this item's values
                print("Reset item with id: " + str(i))
                items[i] = [-1,-1,-1,-1,-1,0,0]

    return

# Threads functions

def camera_thread():

    # Camera def
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(main={"format": "RGB888", "size": (camW, camH)}, controls={'FrameRate' : camFPS})
    picam2.configure(config)
    picam2.set_controls({"AeEnable": False, "ExposureTime": 4000})
    picam2.set_controls({"AnalogueGain": 6.0})
    picam2.start()

    # Buffering
    time.sleep(2)

    # Function def
    # def validate_frame(cap_obj):
    #     for _ in range(5):
    #         ret, frame = cap_obj.read()
    #         if ret and frame is not None:
    #             return True, frame
    #         time.sleep(0.1)
    #     return False, None

    # Shared memory
    cam_shMem = shared_memory.SharedMemory(name=shName)
    sh_buf = np.ndarray((camH, camW, 3), dtype=np.uint8, buffer=cam_shMem.buf)

    while True: 
        # Picamera capture
        # frameReady.wait()
        frame = picam2.capture_array()
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2RGBA)
        # if in_queue.full():
        #     try: in_queue.get_nowait()
        #     except queue.Empty: pass

        # Saving to sh memory
        # frameReady.clear()
        if not in_queue.full():
            in_queue.put(frame_bgr[:, :, :3])
        # frameReady.set()
        # in_queue.put(roi)

def ml_thread():
    # Declarations
    model_version_name = 'detect_v2.tflite'
    cont = 0
    isLabeled = -1
    flag = 0
    result = defaultArray.copy()
    ml_shMem = shared_memory.SharedMemory(name=shName)
    sh_buf = np.ndarray((camH, camW, 3), dtype=np.uint8, buffer=ml_shMem.buf)

    # Define and parse input arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--modeldir', help='Path to the trained model directory',
                        default= "/home/raspberry/Desktop/Trained_Model/custom_model_lite" )
    parser.add_argument('--graph', help='Name of the .tflite file, if different than detect.tflite',
                        default= model_version_name)
    parser.add_argument('--labels', help='Name of the labelmap file, if different than labelmap.txt',
                        default='labelmap.txt')
    parser.add_argument('--threshold', help='Minimum confidence threshold for displaying detected objects',
                        default=0.87) # Default 0.87
    parser.add_argument('--resolution', help='Desired webcam resolution in WxH format',
                        default='800x480')
    parser.add_argument('--edgetpu', help='Use Coral Edge TPU Accelerator to speed up detection',
                        action='store_true')

    args = parser.parse_args()

    MODEL_NAME = args.modeldir
    GRAPH_NAME = args.graph
    LABELMAP_NAME = args.labels
    min_conf_threshold = float(args.threshold)
    resW, resH = args.resolution.split('x')
    imW, imH = int(resW), int(resH)
    use_TPU = args.edgetpu

    # Import TensorFlow libraries
    pkg = importlib.util.find_spec('tflite_runtime')
    if pkg:
        from tflite_runtime.interpreter import Interpreter
        if use_TPU:
            from tflite_runtime.interpreter import load_delegate
    else:
        from tensorflow.lite.python.interpreter import Interpreter
        if use_TPU:
            from tensorflow.lite.python.interpreter import load_delegate

    # Load model and labels
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
                                num_threads = 4)
    else:
        interpreter = Interpreter(model_path=PATH_TO_CKPT, num_threads = 4)
    interpreter.allocate_tensors()

    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    height = input_details[0]['shape'][1]
    width = input_details[0]['shape'][2]
    floating_model = (input_details[0]['dtype'] == np.float32)

    input_mean = 127.5
    input_std = 127.5

    outname = output_details[0]['name']
    if 'StatefulPartitionedCall' in outname:
        boxes_idx, classes_idx, scores_idx = 1, 3, 0
    else:
        boxes_idx, classes_idx, scores_idx = 0, 1, 2

    while True:
        try: 
            # frameReady.wait()
            frameReady.clear()
            flag = 0
            isLabeled = -1
            tStart = time.time()
            frame = in_queue.get_nowait().copy()
            roiFrame = frame[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]
            frame_resized = cv2.resize(roiFrame, (width, height))
            input_data = np.expand_dims(frame_resized, axis=0)

            if floating_model:
                input_data = (np.float32(input_data) - input_mean) / input_std

            interpreter.set_tensor(input_details[0]['index'], input_data)
            interpreter.invoke()

            boxes = interpreter.get_tensor(output_details[boxes_idx]['index'])[0]
            classes = interpreter.get_tensor(output_details[classes_idx]['index'])[0]
            scores = interpreter.get_tensor(output_details[scores_idx]['index'])[0]

            # Resetting results array
            result = defaultArray.copy()
            # print("Result after resetting: " + str(defaultArray[0][0]))

            # for i in range(len(scores)):
            for i in range(5):
                if (scores[i] > min_conf_threshold) and (scores[i] <= 1.0) and i < 5: 
                    flag = 1
                    ymin = int(max(1, (boxes[i][0] * imH)))
                    xmin = int(max(1, (boxes[i][1] * imW)))
                    ymax = int(min(imH, (boxes[i][2] * imH)))
                    xmax = int(min(imW, (boxes[i][3] * imW)))

                    # Define color based on the detected object class
                    if labels[int(classes[i])] == "labeled":
                        isLabeled = 1
                    elif labels[int(classes[i])] == "NO_labeled":
                        # cont = cont +1 
                        cont = 3    # Counter disabled
                        if cont > 2:
                            cont = 0
                            isLabeled = 0
                            print("No labeled")
                    else: isLabeled = -1

                    object_name = labels[int(classes[i])]
                    label = f'{object_name}: {int(scores[i]*100)}%'
                    labelSize, baseLine = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                    label_ymin = max(ymin, labelSize[1] + 10)
                    
                    # Saving results
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
                    isLabeled = -1
                    result[i][0] = isLabeled
                    break

            tEnd = time.time()
            fps = 1 / (tEnd - tStart)
            result[0][9] = fps
            result[0][10] = tEnd


            # print("Result in ML Thread: " + str(result[0][0]))
            # Output frame
            if out_queue.full():
                try: out_queue.get_nowait()
                except queue.Empty: pass
            out_queue.put(result)
            # Flag
            if flag_queue.full():
                try: flag_queue.get_nowait()
                except queue.Empty: pass
            flag_queue.put(flag)
            # Output img
            if out_img_queue.full():
                try: out_img_queue.get_nowait()
                except queue.Empty: pass
            out_img_queue.put(frame)

            frameReady.set()
        except queue.Empty:
            pass

def gpio_thread():
    global badBottle
    # Turning pin ON if bottle is not labeled, testing
    while True:
        # try:
            # Enable the cylinder when the badBottle variable is set to -1, then resets it
        if(badBottle == -1):
            pinRelay.on()
            badBottle = 0
            print("Pin has been turned on...")
            time.sleep(2.5)
            pinRelay.off() 
        time.sleep(0.1)        

        # except queue.Empty:
        #     pass

def bottleDetected():
    global bottleCounter 
    bottleCounter += 1
    print(f"Bottle counter: {bottleCounter}")

def bottleDetected_2():
    global bottleCounter_2 
    bottleCounter_2 += 1
    print(f"Bottle counter: {bottleCounter_2}")


def gpio_counter():
    # Setting the input for the first counter
    pinSensor_1 = Button(17, pull_up=True)
    # When a bottle is detected
    pinSensor_1.when_activated = bottleDetected
    global badBottle
    global bottleCounter

    while(True):
        # Checks for the unlabeled bottle flag
        try:
            flag = flag_queue.get()
            if(flag):
                badBottle = bottleCounter
            # time.sleep(100)
        except queue.Empty:
            pass

def gpio_counter_2():
    # Setting the input for the first counter
    pinSensor_2 = Button(27, pull_up=True)
    # When a bottle is detected
    pinSensor_2.when_activated = bottleDetected_2
    global badBottle
    global bottleCounter

    while(True):
        # Checking if there is a badbottle, and whether it should be ejected
        # try:
        if(badBottle == bottleCounter_2):
            # Enable the output cylinder
            badBottle = -1  # Change to queue
        time.sleep(0.1)

        # except queue.Empty:
        #     pass


if __name__ == "__main__":
    # Creating threads
    thread1 = threading.Thread(target=camera_thread, daemon=True)
    thread2 = threading.Thread(target=ml_thread, daemon=True)
    thread3 = threading.Thread(target=gpio_thread, daemon=True)
    thread4 = threading.Thread(target=gpio_counter, daemon=True)
    thread5 = threading.Thread(target=gpio_counter_2, daemon=True)
    # Starting threads
    thread1.start()
    thread2.start()
    thread3.start()
    thread4.start()
    thread5.start()
    # Setting up Window
    winName = "Detector de botellas"
    cv2.namedWindow(winName, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(winName, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    # Initializing
    result = defaultArray.copy()
    lastDetection = result.copy()
    c = 0

    while True:
        try:
            frameReady.wait()
            
            # Get ML Results
            result = out_queue.get_nowait()
            processed_frame = out_img_queue.get_nowait()
                        
            # result[0] = isLabeled
            # result[1] = xmin
            # result[2] = xmax
            # result[3] = ymin
            # result[4] = ymax
            # result[5] = label_ymin
            # result[6] = labelSize[0]
            # result[7] = labelSize[1]
            # result[8] = baseLine
            # result[9] = FPS

            # Check if there is a detection
            print("Result[0][0] = " + str(result[0][0]))
            if (result[0][0] == -1):
                c += 1
                if (c >= 10):
                    # print("No items identified")
                    lastDetection = defaultArray.copy()
            else:
                c = 0
                lastDetection = result

            # Check if there are detections
            print("C= " + str(c))
            if (lastDetection[0][0] != -1 and c < 10):
                # at least 1 item detected
                for i in range(len(lastDetection)):
                    # If an item is not detected, break
                    if (lastDetection[i][0] == 1): 
                        label = "Labeled"
                    elif (lastDetection[i][0] == 0):
                        label = "No Labeled"
                    else:
                        print("Item not detected")
                        c += 1    
                        break
                    # Print boxes
                    cv2.rectangle(processed_frame, (lastDetection[i][1], lastDetection[i][3]), (lastDetection[i][2], lastDetection[i][4]), (10,255,0), 2)
                    # White box for text
                    cv2.rectangle(processed_frame, (lastDetection[i][1], lastDetection[i][5]-lastDetection[i][7]-10), (lastDetection[i][1]+lastDetection[i][6], lastDetection[i][5]+lastDetection[i][8]-10), (255, 255, 255), cv2.FILLED)
                    cv2.putText(processed_frame, label + " id: " + str(i), (lastDetection[i][1], lastDetection[i][5]-7), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
            # Print standard UI
            # ROI
            cv2.rectangle(processed_frame, (roi_x, roi_y), (roi_x + roi_w, roi_y + roi_h), (10, 255, 0), 1)
            # FPS
            cv2.putText(processed_frame, f'FPS: {result[0][9]:.2f}', (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2, cv2.LINE_AA)        

            # Resizing image
            resizedFrame = cv2.resize(processed_frame, (camW * 1, camH * 1), interpolation=cv2.INTER_LINEAR)
            cv2.imshow(winName, resizedFrame)
        except queue.Empty:
            # print("Setting frame ready flag")
            # frameReady.set()
            pass
        # Quit on 'q'
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('f'):
            cv2.setWindowProperty(winName, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)

# Cleaning up
cv2.destroyAllWindows()
shMem.close()
shMem.unlink()
