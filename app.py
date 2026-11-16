import os
import threading
import time
import tkinter as tk
import state
import detector
import recorder
import utils

root_window = None


def set_exit_state():
    state.app_state = 'exit'
    state.mode_event.set()


def exit_watchdog():
    stop_file = os.path.join(os.getcwd(), 'stop_detector')
    while state.app_state != 'exit':
        if os.path.exists(stop_file):
            state.status_message = 'Solicitud de salida detectada'
            set_exit_state()
            try:
                os.remove(stop_file)
            except Exception:
                pass
            break
        time.sleep(1)


def select_mode(mode):
    global root_window
    if state.selected_mode is not None:
        return
    state.selected_mode = mode
    state.app_state = mode
    state.status_message = f'Modo {mode} seleccionado'
    utils.run_update_script()

    if mode == 'detect':
        detector.start_detection()
    elif mode == 'record':
        recorder_thread = threading.Thread(target=recorder.record_video, daemon=True)
        recorder_thread.start()

    if root_window is not None:
        root_window.withdraw()




def create_start_interface():
    global root_window
    root_window = tk.Tk()
    root = root_window


    # ---- CONFIGURACIÓN BÁSICA DE VENTANA ----
    root.title('Sistema de inspección de botellas')
    root.configure(bg='#1E1E1E')  # gris oscuro más moderno


    fullscreen_enabled = True
    last_e_press = 0.0
    e_press_count = 0


    def set_fullscreen(enabled):

        root.attributes('-fullscreen', True)
        
    set_fullscreen(True)


    def on_close():
        set_exit_state()
        root.destroy()


    root.protocol('WM_DELETE_WINDOW', on_close)


    # ---- ESTILOS REUTILIZABLES ----
    BG_MAIN = '#1E1E1E'
    BG_CARD = '#2A2A2A'
    BG_HEADER = '#262626'
    FG_TEXT = '#F0F0F0'
    FG_MUTED = '#B0B0B0'
    ACCENT = '#00B4D8'
    ACCENT_WARN = '#FFB703'
    ACCENT_ERROR = '#E63946'
    ACCENT_OK = '#06D6A0'


    root.configure(bg=BG_MAIN)


    # ---- LAYOUT PRINCIPAL: FRAME CONTENEDOR CENTRADO ----
    container = tk.Frame(root, bg=BG_MAIN)
    container.pack(expand=True, fill='both', padx=40, pady=40)


    # Usamos una "tarjeta" central
    card = tk.Frame(container, bg=BG_CARD, bd=0, relief='flat', highlightthickness=1)
    card.place(relx=0.5, rely=0.5, anchor='center', relwidth=0.8, relheight=0.8)


    # Para que se vea como tarjeta con borde sutil
    card.configure(highlightbackground='#444444', highlightcolor='#444444')


    # ---- HEADER ----
    header = tk.Frame(card, bg=BG_HEADER, height=60)
    header.pack(side='top', fill='x')


    title_label = tk.Label(
        header,
        text='Sistema de Inspección de Botellas',
        font=('Arial', 22, 'bold'),
        fg=FG_TEXT,
        bg=BG_HEADER
    )
    title_label.pack(side='left', padx=20, pady=10)


    subtitle_label = tk.Label(
        header,
        text='Seleccione modo o espere la cuenta regresiva',
        font=('Arial', 12),
        fg=FG_MUTED,
        bg=BG_HEADER
    )
    subtitle_label.pack(side='right', padx=20)


    # ---- CONTENIDO PRINCIPAL: 2 COLUMNAS ----
    content = tk.Frame(card, bg=BG_CARD)
    content.pack(expand=True, fill='both', padx=20, pady=20)


    # Columnas: izquierda (cuenta + botones), derecha (estado)
    content.columnconfigure(0, weight=1)
    content.columnconfigure(1, weight=1)


    # === COLUMNA IZQUIERDA ===
    left_frame = tk.Frame(content, bg=BG_CARD)
    left_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 10))


    # Bloque de cuenta regresiva
    countdown_title = tk.Label(
        left_frame,
        text='Inicio automático en:',
        font=('Arial', 16),
        fg=FG_TEXT,
        bg=BG_CARD
    )
    countdown_title.pack(pady=(10, 0))


    countdown_label = tk.Label(
        left_frame,
        text=str(state.countdown_value),
        font=('Arial', 72, 'bold'),
        fg=ACCENT,
        bg=BG_CARD
    )
    countdown_label.pack(pady=(0, 10))


    mode_label = tk.Label(
        left_frame,
        text='Teclas rápidas:\n[D] Detectar    [V] Grabar video    [E] Salir (doble pulsación)',
        font=('Arial', 12),
        fg=FG_MUTED,
        bg=BG_CARD,
        justify='center'
    )
    mode_label.pack(pady=(0, 20))


    # Botones de acción
    buttons_frame = tk.Frame(left_frame, bg=BG_CARD)
    buttons_frame.pack(pady=10)


    detect_button = tk.Button(
        buttons_frame,
        text='▶  Iniciar DETECCIÓN (D)',
        font=('Arial', 15, 'bold'),
        command=lambda: select_mode('detect'),
        width=26,
        bg='#0077B6',
        fg='white',
        activebackground='#0096C7',
        activeforeground='white',
        bd=0,
        relief='flat',
        padx=10,
        pady=6
    )
    detect_button.pack(pady=(0, 10))


    record_button = tk.Button(
        buttons_frame,
        text='●  Grabar VIDEO (V)',
        font=('Arial', 15, 'bold'),
        command=lambda: select_mode('record'),
        width=26,
        bg='#8338EC',
        fg='white',
        activebackground='#9D4EDD',
        activeforeground='white',
        bd=0,
        relief='flat',
        padx=10,
        pady=6
    )
    record_button.pack(pady=(0, 10))


    # Nota al pie en la columna izquierda
    hint_label = tk.Label(
        left_frame,
        text='Presione ESC para salir de pantalla completa.\nLa detección iniciará automáticamente al finalizar la cuenta regresiva.',
        font=('Arial', 10),
        fg=FG_MUTED,
        bg=BG_CARD,
        justify='center'
    )
    hint_label.pack(side='bottom', pady=(10, 0))


    # === COLUMNA DERECHA ===
    right_frame = tk.Frame(content, bg=BG_CARD)
    right_frame.grid(row=0, column=1, sticky='nsew', padx=(10, 0))


    # Panel de estado
    status_card = tk.LabelFrame(
        right_frame,
        text=' Estado del sistema ',
        font=('Arial', 12, 'bold'),
        fg=FG_TEXT,
        bg=BG_CARD,
        bd=1,
        relief='groove',
        labelanchor='nw'
    )
    status_card.pack(fill='both', expand=True, pady=(0, 10))


    status_label = tk.Label(
        status_card,
        text=state.status_message,
        font=('Arial', 13),
        fg=ACCENT_WARN,
        bg=BG_CARD,
        wraplength=400,
        justify='left'
    )
    status_label.pack(anchor='w', padx=10, pady=(10, 5))


    # Panel de métricas
    metrics_card = tk.LabelFrame(
        right_frame,
        text=' Contadores ',
        font=('Arial', 12, 'bold'),
        fg=FG_TEXT,
        bg=BG_CARD,
        bd=1,
        relief='groove',
        labelanchor='nw'
    )
    metrics_card.pack(fill='x', pady=(0, 10))


    bottle_label = tk.Label(
        metrics_card,
        text=f'Botellas entrada (S1): {state.bottleCounter}\nBotellas expulsión (S2): {state.bottleCounter_2}',
        font=('Arial', 13),
        fg=FG_TEXT,
        bg=BG_CARD,
        justify='left'
    )
    bottle_label.pack(anchor='w', padx=10, pady=(10, 5))


    relay_label = tk.Label(
        metrics_card,
        text='Electrovalvula: OFF',
        font=('Arial', 13, 'bold'),
        fg=ACCENT_OK,
        bg=BG_CARD
    )
    relay_label.pack(anchor='w', padx=10, pady=(0, 10))


    # Pie pequeño a la derecha
    footer = tk.Label(
        right_frame,
        text='Doble pulsación de [E] para salir de la aplicación.',
        font=('Arial', 10),
        fg=FG_MUTED,
        bg=BG_CARD,
        justify='left'
    )
    footer.pack(anchor='w', pady=(0, 5), padx=4)


    # ---- LÓGICA DE ACTUALIZACIÓN (MISMA QUE TENÍAS) ----
    def update_labels():
        # Actualiza textos dinámicos
        countdown_label.config(text=str(state.countdown_value))
        status_label.config(text=state.status_message)
        bottle_label.config(
            text=f'Botellas entrada (S1): {state.bottleCounter}\nBotellas expulsión (S2): {state.bottleCounter_2}'
        )


        if state.relay_active:
            relay_label.config(text='Electrovalvula ACTIVADA', fg=ACCENT_ERROR)
        else:
            relay_label.config(text='Electrovalvula: OFF', fg=ACCENT_OK)


        # Manejo de cierre
        if state.app_state == 'exit':
            root.destroy()
            return


        # Si vuelve a idle sin modo seleccionado, mostrar ventana otra vez
        if state.app_state == 'idle' and state.selected_mode is None and not root.winfo_viewable():
            root.deiconify()


        root.after(200, update_labels)


    def tick():
        if state.selected_mode is not None or state.app_state == 'exit':
            return
        if state.countdown_value <= 0:
            select_mode('detect')
            return
        state.countdown_value -= 1
        countdown_label.config(text=str(state.countdown_value))
        root.after(1000, tick)


    def on_key(event):
        nonlocal last_e_press, e_press_count
        key = event.keysym.lower()
        now = time.time()


        # Salir con doble E
        if key == 'e':
            if now - last_e_press < 1.0:
                e_press_count += 1
            else:
                e_press_count = 1
            last_e_press = now
            if e_press_count >= 2:
                set_exit_state()
            return
        else:
            e_press_count = 0


        # Selección de modo
        if key == 'd':
            select_mode('detect')
        elif key == 'v':
            select_mode('record')


    # ---- BINDINGS Y ARRANQUE ----
    root.bind('<Key>', on_key)
    # root.bind('<Escape>', lambda event: set_fullscreen(False))


    threading.Thread(target=exit_watchdog, daemon=True).start()
    root.after(1000, tick)
    update_labels()


    root.mainloop()