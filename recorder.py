import os
import time
import cv2
from datetime import datetime
from picamera2 import Picamera2
import state
from utils import ensure_dir


def video_to_images(video_path, output_folder=None, fps_extract=4, segment_seconds=10, delete_video=True):
    if output_folder is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_folder = os.path.join(os.getcwd(), f'frames_{timestamp}')
    ensure_dir(output_folder)

    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        print(f"Error abriendo el video {video_path}")
        return output_folder

    video_fps = capture.get(cv2.CAP_PROP_FPS) or state.camFPS
    frame_interval = max(1, int(round(video_fps / fps_extract)))
    segment_frames = max(1, int(round(video_fps * segment_seconds)))
    current_part = 1
    part_folder = os.path.join(output_folder, f'part_{current_part:02d}')
    ensure_dir(part_folder)

    index = 0
    saved = 0

    while True:
        ret, frame = capture.read()
        if not ret:
            break
        if index % frame_interval == 0:
            part = (index // segment_frames) + 1
            if part != current_part:
                current_part = part
                part_folder = os.path.join(output_folder, f'part_{current_part:02d}')
                ensure_dir(part_folder)
            image_path = os.path.join(part_folder, f'frame_{saved:04d}.jpg')
            cv2.imwrite(image_path, frame)
            saved += 1
        index += 1

    capture.release()
    if delete_video:
        try:
            os.remove(video_path)
            print(f'Video temporal eliminado: {video_path}')
        except Exception as e:
            print(f'No se pudo borrar video temporal: {e}')
    print(f"Extraídas {saved} imágenes en {output_folder}")
    return output_folder


def record_video(duration=60):
    state.status_message = f"Grabando video... {duration}s máximo"
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_folder = os.path.join(os.getcwd(), f'recording_{timestamp}')
    ensure_dir(base_folder)
    video_path = os.path.join(base_folder, f'recording_{timestamp}.mp4')

    try:
        picam = Picamera2()
        config = picam.create_preview_configuration(main={"format": "RGB888", "size": (state.camW, state.camH)}, controls={'FrameRate': state.camFPS})
        picam.configure(config)
        picam.start()
        time.sleep(1)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(video_path, fourcc, state.camFPS, (state.camW, state.camH))

        start_time = time.time()
        while time.time() - start_time < duration and state.app_state == 'record':
            frame = picam.capture_array()
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            writer.write(frame_bgr)
            time.sleep(1 / state.camFPS)

        writer.release()
        picam.stop()
        state.status_message = f"Video guardado en {video_path}"
        extracted_folder = video_to_images(video_path, output_folder=os.path.join(base_folder, 'frames'), fps_extract=4, segment_seconds=10, delete_video=True)
        state.status_message = f"Extracción completa: {extracted_folder} (video eliminado)"
    except Exception as e:
        state.status_message = f"Error de grabación: {e}"
        print(state.status_message)
    finally:
        if state.app_state != 'exit':
            state.app_state = 'idle'
            state.selected_mode = None
            state.status_message = 'Grabación completada. Seleccione modo.'
