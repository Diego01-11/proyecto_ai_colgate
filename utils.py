import os
import subprocess
import sys


def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    return path


def run_update_script():
    update_file = os.path.join(os.getcwd(), 'update.py')
    if os.path.exists(update_file):
        try:
            subprocess.Popen([sys.executable, update_file])
            print(f"Ejecutando script de actualización: {update_file}")
        except Exception as e:
            print(f"No se pudo ejecutar update.py: {e}")
    else:
        print("No se encontró update.py, continuando con la aplicación principal.")
