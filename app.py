import base64
import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk

import detector
import recorder
import state
import utils


BG_MAIN = "#0F172A"
BG_CARD = "#1E293B"
BG_CARD_ALT = "#263449"
FG_TEXT = "#F8FAFC"
FG_MUTED = "#94A3B8"
ACCENT = "#38BDF8"
ACCENT_OK = "#22C55E"
ACCENT_WARN = "#F59E0B"
ACCENT_ERROR = "#EF4444"

root_window = None
calibration_window = None


def minimize_for_maintenance():
    with state.vision_lock:
        state.maintenance_minimize_requested = True


def set_exit_state():
    state.app_state = "exit"
    state.mode_event.set()


def exit_watchdog():
    stop_file = os.path.join(os.getcwd(), "stop_detector")
    while state.app_state != "exit":
        if os.path.exists(stop_file):
            state.status_message = "Solicitud de salida detectada"
            set_exit_state()
            try:
                os.remove(stop_file)
            except Exception:
                pass
            break
        time.sleep(1)


def _load_vision_config():
    config_path = os.path.join(os.getcwd(), "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = json.load(config_file)
        settings = config["vision"]
        camera = config.get("camera", {})
        loaded = {
            "enable_label_edge_check": bool(settings["enable_label_edge_check"]),
            "min_label_gx": float(settings["min_label_gx"]),
            "min_label_gx_gy_ratio": float(settings["min_label_gx_gy_ratio"]),
            "min_purple_ratio": float(settings["min_purple_ratio"]),
        }
        loaded_camera = {
            "auto_exposure": camera.get(
                "auto_exposure", state.camera_settings["auto_exposure"]
            ),
            "exposure_time_us": int(camera.get(
                "exposure_time_us", state.camera_settings["exposure_time_us"]
            )),
            "analogue_gain": float(camera.get(
                "analogue_gain", state.camera_settings["analogue_gain"]
            )),
        }
        if not isinstance(loaded_camera["auto_exposure"], bool):
            raise ValueError("camera.auto_exposure debe ser booleano")
        if not (0 <= loaded["min_label_gx"] <= 100):
            raise ValueError("MIN_LABEL_GX fuera del rango 0–100")
        if not (0 <= loaded["min_label_gx_gy_ratio"] <= 2):
            raise ValueError("MIN_LABEL_GX_GY_RATIO fuera del rango 0–2")
        if not (0 <= loaded["min_purple_ratio"] <= 1):
            raise ValueError("MIN_PURPLE_RATIO fuera del rango 0–1")
        if not (100 <= loaded_camera["exposure_time_us"] <= 30000):
            raise ValueError("camera.exposure_time_us fuera del rango 100–30000 µs")
        if not (1.0 <= loaded_camera["analogue_gain"] <= 16.0):
            raise ValueError("camera.analogue_gain fuera del rango 1–16")
    except FileNotFoundError:
        return "Usando umbrales iniciales; config.json aún no existe."
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return f"Error leyendo config.json: {error}"

    with state.vision_lock:
        state.vision_settings.update(loaded)
    with state.camera_control_lock:
        state.camera_settings.update(loaded_camera)
    return "Umbrales y cámara cargados desde config.json."


def select_mode(mode):
    if state.selected_mode is not None:
        return
    state.selected_mode = mode
    state.app_state = mode
    state.status_message = f"Modo {mode} seleccionado"
    utils.run_update_script()

    if mode == "detect":
        detector.start_detection()
        if calibration_window is not None:
            calibration_window.deiconify()
            calibration_window.attributes("-topmost", True)
    elif mode == "record":
        threading.Thread(
            target=recorder.record_video,
            kwargs={"duration": 30},
            daemon=True,
        ).start()


def create_start_interface():
    global calibration_window
    global root_window

    config_status = _load_vision_config()
    root = tk.Tk()
    root_window = root
    root.title("Sistema de inspección de botellas")
    root.configure(bg=BG_MAIN)
    root.attributes("-fullscreen", True)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG_MAIN, foreground=FG_TEXT, font=("Segoe UI", 11))
    style.configure("TFrame", background=BG_MAIN)
    style.configure("Card.TFrame", background=BG_CARD)
    style.configure("TLabel", background=BG_MAIN, foreground=FG_TEXT)
    style.configure("Card.TLabel", background=BG_CARD, foreground=FG_TEXT)
    style.configure("Muted.TLabel", background=BG_CARD, foreground=FG_MUTED)
    style.configure("Title.TLabel", background=BG_CARD, foreground=FG_TEXT, font=("Segoe UI", 24, "bold"))
    style.configure(
        "Primary.TButton",
        background="#0284C7",
        foreground=FG_TEXT,
        padding=(24, 16),
        font=("Segoe UI", 15, "bold"),
        borderwidth=0,
    )
    style.map("Primary.TButton", background=[("active", "#0369A1")])
    style.configure(
        "Record.TButton",
        background="#7C3AED",
        foreground=FG_TEXT,
        padding=(24, 16),
        font=("Segoe UI", 15, "bold"),
        borderwidth=0,
    )
    style.map("Record.TButton", background=[("active", "#6D28D9")])
    style.configure(
        "TProgressbar",
        troughcolor=BG_CARD_ALT,
        background=ACCENT,
        bordercolor=BG_CARD,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
    )
    style.configure("TScale", background=BG_CARD, troughcolor=BG_CARD_ALT)

    last_e_press = 0.0
    e_press_count = 0
    gallery_images = []
    gallery_labels = []
    last_gallery_version = -1

    shell = ttk.Frame(root, padding=32)
    shell.pack(fill="both", expand=True)
    header = ttk.Frame(shell, style="Card.TFrame", padding=(24, 18))
    header.pack(fill="x", pady=(0, 20))
    title = ttk.Label(header, text="BOTELLA / INSPECCIÓN VISUAL", style="Title.TLabel")
    title.pack(side="left")
    title.bind("<Triple-Button-1>", lambda event: minimize_for_maintenance())
    ttk.Label(
        header,
        text="DARK INDUSTRIAL  •  Ctrl+Shift+M / F12: mantenimiento",
        style="Muted.TLabel",
    ).pack(side="right")

    body = ttk.Frame(shell)
    body.pack(fill="both", expand=True)
    body.columnconfigure(0, weight=3)
    body.columnconfigure(1, weight=2)
    controls = ttk.Frame(body, style="Card.TFrame", padding=28)
    controls.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
    status_card = ttk.Frame(body, style="Card.TFrame", padding=28)
    status_card.grid(row=0, column=1, sticky="nsew", padx=(12, 0))

    ttk.Label(controls, text="PANEL DE OPERACIÓN", style="Card.TLabel", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    countdown_label = ttk.Label(controls, text=str(state.countdown_value), style="Card.TLabel", font=("Segoe UI", 64, "bold"), foreground=ACCENT)
    countdown_label.pack(anchor="w", pady=(22, 0))
    ttk.Label(controls, text="segundos para inicio automático de inspección", style="Muted.TLabel").pack(anchor="w", pady=(0, 24))

    buttons = ttk.Frame(controls, style="Card.TFrame")
    buttons.pack(anchor="w", fill="x")
    ttk.Button(
        buttons,
        text="▶  INICIAR DETECCIÓN",
        style="Primary.TButton",
        command=lambda: select_mode("detect"),
    ).pack(fill="x", pady=(0, 12))
    ttk.Button(
        buttons,
        text="●  GRABAR VIDEO 30 s",
        style="Record.TButton",
        command=lambda: select_mode("record"),
    ).pack(fill="x")

    ttk.Separator(controls).pack(fill="x", pady=26)
    record_message = tk.StringVar(value="Listo para iniciar")
    ttk.Label(controls, textvariable=record_message, style="Card.TLabel").pack(anchor="w")
    progress = ttk.Progressbar(controls, maximum=100, mode="determinate")
    progress.pack(fill="x", pady=(10, 8))
    ttk.Label(
        controls,
        text="Atajos: D detectar  •  V grabar  •  E dos veces salir  •  F12/Ctrl+Shift+M mantenimiento",
        style="Muted.TLabel",
        wraplength=800,
    ).pack(anchor="w", pady=(20, 0))

    status_title = ttk.Label(status_card, text="ESTADO DEL SISTEMA", style="Card.TLabel", font=("Segoe UI", 14, "bold"))
    status_title.pack(anchor="w", pady=(0, 16))
    status_message = tk.StringVar(value=state.status_message or config_status)
    ttk.Label(status_card, textvariable=status_message, style="Card.TLabel", wraplength=450).pack(anchor="w")
    bottle_counts = tk.StringVar()
    ttk.Label(status_card, textvariable=bottle_counts, style="Card.TLabel", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(28, 12))
    relay_status = tk.StringVar()
    ttk.Label(status_card, textvariable=relay_status, style="Card.TLabel").pack(anchor="w")

    calibration_window = tk.Toplevel(root)
    calibration_window.title("Calibración de visión")
    calibration_window.configure(bg=BG_MAIN)
    calibration_window.geometry("720x760")
    calibration_window.attributes("-topmost", True)
    calibration_window.withdraw()

    cal_shell = ttk.Frame(calibration_window, padding=20)
    cal_shell.pack(fill="both", expand=True)
    ttk.Label(cal_shell, text="CALIBRACIÓN DE ETIQUETA", font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 14))
    edge_check_var = tk.BooleanVar()
    with state.vision_lock:
        edge_check_var.set(state.vision_settings["enable_label_edge_check"])
    ttk.Checkbutton(
        cal_shell,
        text="Activar filtro de bordes",
        variable=edge_check_var,
        command=lambda: update_setting("enable_label_edge_check", edge_check_var.get()),
    ).pack(anchor="w", pady=(0, 16))

    camera_auto_var = tk.BooleanVar()
    with state.camera_control_lock:
        camera_auto_var.set(state.camera_settings["auto_exposure"])

    def update_camera_setting(**settings):
        try:
            detector.set_camera_settings(**settings)
        except (RuntimeError, ValueError, OSError) as error:
            config_feedback.set(f"No se pudo aplicar control de cámara: {error}")

    ttk.Checkbutton(
        cal_shell,
        text="Auto-exposición",
        variable=camera_auto_var,
        command=lambda: update_camera_setting(auto_exposure=camera_auto_var.get()),
    ).pack(anchor="w", pady=(0, 12))

    with state.camera_control_lock:
        camera_settings_snapshot = dict(state.camera_settings)
    exposure_var = tk.DoubleVar(value=camera_settings_snapshot["exposure_time_us"])
    gain_var = tk.DoubleVar(value=camera_settings_snapshot["analogue_gain"])
    ttk.Label(cal_shell, text="Exposición manual (µs)").pack(anchor="w")
    ttk.Scale(
        cal_shell,
        from_=100,
        to=30000,
        variable=exposure_var,
        command=lambda value: update_camera_setting(exposure_time_us=value),
    ).pack(fill="x", pady=(3, 10))
    ttk.Label(cal_shell, text="Ganancia analógica").pack(anchor="w")
    ttk.Scale(
        cal_shell,
        from_=1,
        to=16,
        variable=gain_var,
        command=lambda value: update_camera_setting(analogue_gain=value),
    ).pack(fill="x", pady=(3, 12))

    metric_vars = {
        "min_label_gx": tk.DoubleVar(),
        "min_label_gx_gy_ratio": tk.DoubleVar(),
        "min_purple_ratio": tk.DoubleVar(),
    }

    def update_setting(key, value):
        with state.vision_lock:
            state.vision_settings[key] = value

    slider_options = (
        ("MIN_LABEL_GX", "min_label_gx", 0, 100),
        ("MIN_LABEL_GX_GY_RATIO", "min_label_gx_gy_ratio", 0, 2),
        ("MIN_PURPLE_RATIO", "min_purple_ratio", 0, 1),
    )
    for label_text, key, lower, upper in slider_options:
        with state.vision_lock:
            metric_vars[key].set(state.vision_settings[key])
        row = ttk.Frame(cal_shell)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text=label_text, width=28).pack(side="left")
        value_label = ttk.Label(row, textvariable=metric_vars[key], width=7)
        value_label.pack(side="right")
        ttk.Scale(
            row,
            from_=lower,
            to=upper,
            variable=metric_vars[key],
            command=lambda value, setting=key: update_setting(setting, float(value)),
        ).pack(side="left", fill="x", expand=True, padx=10)

    vision_readout = tk.StringVar(value="Esperando inferencia...")
    ttk.Label(cal_shell, textvariable=vision_readout, wraplength=660).pack(anchor="w", pady=10)
    config_feedback = tk.StringVar(value=config_status)
    ttk.Label(cal_shell, textvariable=config_feedback, wraplength=660).pack(anchor="w", pady=(0, 8))

    def save_config():
        with state.vision_lock:
            saved_settings = dict(state.vision_settings)
        with state.camera_control_lock:
            saved_camera = dict(state.camera_settings)
        config_path = os.path.join(os.getcwd(), "config.json")
        try:
            with open(config_path, "w", encoding="utf-8") as config_file:
                json.dump(
                    {"vision": saved_settings, "camera": saved_camera},
                    config_file,
                    indent=2,
                )
        except OSError as error:
            config_feedback.set(f"No se pudo guardar config.json: {error}")
        else:
            config_feedback.set(f"Umbrales guardados en {config_path}")

    ttk.Button(cal_shell, text="Guardar umbrales en config.json", command=save_config).pack(anchor="w", pady=(4, 8))

    def request_test_capture():
        with state.vision_lock:
            state.capture_test_requested = True
        config_feedback.set("Se capturará el próximo frame disponible.")

    ttk.Button(cal_shell, text="Capturar foto de prueba", command=request_test_capture).pack(anchor="w", pady=(0, 12))
    gallery_frame = ttk.Frame(cal_shell)
    gallery_frame.pack(fill="both", expand=True)
    gallery_title = ttk.Label(gallery_frame, text="ÚLTIMOS RECHAZOS / PRUEBAS (máx. 4)", font=("Segoe UI", 11, "bold"))
    gallery_title.pack(anchor="w", pady=(0, 8))
    gallery_body = ttk.Frame(gallery_frame)
    gallery_body.pack(fill="both", expand=True)

    def update_calibration_controls():
        with state.vision_lock:
            metrics = dict(state.vision_metrics)
            gallery_version = state.vision_gallery_version
            gallery = list(state.vision_gallery)
        vision_readout.set(
            f"invoke {metrics['invoke_ms']:.1f} ms  |  inferencias/ventana {metrics['window_inf_count']}  |  "
            f"Gx {metrics['mean_gx']:.1f}  Gy {metrics['mean_gy']:.1f}  "
            f"ratio {metrics['ratio']:.3f}  purple {metrics['purple_ratio']:.2f}"
        )
        nonlocal last_gallery_version
        if gallery_version != last_gallery_version:
            for widget in gallery_body.winfo_children():
                widget.destroy()
            gallery_images.clear()
            gallery_labels.clear()
            for index, item in enumerate(reversed(gallery)):
                photo = tk.PhotoImage(data=base64.b64encode(item["image"]).decode("ascii"))
                factor = max(1, (photo.width() + 139) // 140)
                if factor > 1:
                    photo = photo.subsample(factor, factor)
                label = ttk.Label(
                    gallery_body,
                    text=f"{item['kind']}  {item['timestamp']}",
                    image=photo,
                    compound="top",
                )
                label.grid(row=index // 2, column=index % 2, padx=8, pady=6, sticky="nw")
                label.image = photo
                gallery_images.append(photo)
                gallery_labels.append(label)
            last_gallery_version = gallery_version
        root.after(300, update_calibration_controls)

    def maintenance_restore(event=None):
        if state.maintenance_minimized:
            with state.vision_lock:
                state.maintenance_minimized = False
            root.attributes("-fullscreen", True)
            if state.app_state == "detect":
                calibration_window.deiconify()
                calibration_window.attributes("-topmost", True)

    def on_close_calibration():
        calibration_window.withdraw()

    def on_close():
        set_exit_state()
        root.destroy()

    calibration_window.protocol("WM_DELETE_WINDOW", on_close_calibration)
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.bind("<F12>", lambda event: minimize_for_maintenance())
    root.bind("<Control-Shift-m>", lambda event: minimize_for_maintenance())
    root.bind("<Key>", lambda event: on_key(event))
    root.bind("<Map>", maintenance_restore)
    calibration_window.bind("<F12>", lambda event: minimize_for_maintenance())
    calibration_window.bind("<Control-Shift-m>", lambda event: minimize_for_maintenance())
    calibration_window.bind("<Map>", maintenance_restore)

    def on_key(event):
        nonlocal last_e_press, e_press_count
        key = event.keysym.lower()
        now = time.time()
        if key == "e":
            e_press_count = e_press_count + 1 if now - last_e_press < 1.0 else 1
            last_e_press = now
            if e_press_count >= 2:
                set_exit_state()
            return
        e_press_count = 0
        if key == "d":
            select_mode("detect")
        elif key == "v":
            select_mode("record")

    def update_labels():
        nonlocal last_gallery_version
        if state.app_state == "exit":
            root.destroy()
            return
        countdown_label.configure(text=str(state.countdown_value))
        status_message.set(state.status_message or config_status)
        bottle_counts.set(f"S1 ENTRADA  {state.bottleCounter}     S2 SALIDA  {state.bottleCounter_2}")
        relay_status.set("ELECTROVÁLVULA  ACTIVA" if state.relay_active else "ELECTROVÁLVULA  EN REPOSO")
        if state.record_stage in ("recording", "extracting"):
            record_message.set(state.record_message)
            progress["value"] = state.record_progress * 100
        elif state.record_stage == "done":
            record_message.set(state.record_message)
            progress["value"] = 100
        elif state.record_stage == "error":
            record_message.set(state.record_message)
        with state.vision_lock:
            minimize_requested = state.maintenance_minimize_requested
            if minimize_requested:
                state.maintenance_minimize_requested = False
                state.maintenance_minimized = True
        if minimize_requested:
            root.attributes("-fullscreen", False)
            root.iconify()
            if calibration_window.winfo_viewable():
                calibration_window.iconify()
        root.after(200, update_labels)

    def tick():
        if state.selected_mode is not None or state.app_state == "exit":
            return
        if state.countdown_value <= 0:
            select_mode("detect")
            return
        state.countdown_value -= 1
        root.after(1000, tick)

    threading.Thread(target=exit_watchdog, daemon=True).start()
    root.after(1000, tick)
    root.after(300, update_calibration_controls)
    update_labels()
    root.mainloop()
