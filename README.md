# Sistema de inspección de botellas

Proyecto Python para detección visual de botellas, control de relé/electroválvula y grabación de video con Raspberry Pi / cámara compatible con Picamera2.

## Descripción

La aplicación permite:

- iniciar una interfaz gráfica con selección de modo
- detectar botellas con un modelo TensorFlow Lite
- activar una salida GPIO mediante relé o electroválvula
- contar botellas según sensores físicos
- grabar videos y extraer frames en carpetas segmentadas

## Estructura del proyecto

- `app.py`: interfaz principal y gestor de modos
- `detector.py`: flujo de detección, threading y visualización
- `recorder.py`: grabación y extracción de frames
- `state.py`: estado global compartido entre hilos
- `utils.py`: utilidades varias
- `main.py`: punto de entrada
- `requirements.txt`: dependencias del proyecto
- `backup/`: scripts o copias previas

## Requisitos

- Python 3.11
- Raspberry Pi con cámara compatible
- Dependencias listadas en `requirements.txt`

## Instalación

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Ejecución

```bash
python main.py
```

Desde la interfaz se pueden elegir los modos:

- `detect`: detectar botellas y activar la electroválvula
- `record`: grabar video y generar frames

## Nota importante

Los datos generados por la cámara (`recording_*`, `frames_*`, imágenes y videos temporales) se ignoran en Git por seguridad y por tamaño.

## Git

Este directorio está preparado para ser usado como repositorio Git. Una vez que quieras conectarlo a GitHub, puedes hacer:

```bash
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git branch -M main
git push -u origin main
```
