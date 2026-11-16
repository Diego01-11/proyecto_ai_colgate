"""Prueba simple de cámara para Raspberry Pi usando Picamera2.

Este script es independiente del resto del proyecto y solo muestra
la imagen de la cámara en una ventana OpenCV.

esto se hizo el dia 14 de julio 2026 debido a problemas en las  librerias de picamera2 y opencv, para verificar que la camara funciona correctamente.
no implica que el resto del proyecto funcione correctamente, solo es una prueba de la camara.



Ejecutar:
    python camera_test.py
"""

import time
import cv2
from picamera2 import Picamera2


def main():
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(main={"format": "RGB888", "size": (800, 480)})
    picam2.configure(config)
    picam2.start()

    time.sleep(2)

    window_name = "Camera Test"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    try:
        while True:
            frame = picam2.capture_array()
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            cv2.imshow(window_name, frame_bgr)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        picam2.stop()


if __name__ == '__main__':
    main()
