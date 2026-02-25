"""
Stand-Up Reminder - Windows System Tray App
Reminds you to stand up after a configurable interval.
Double-click standup_reminder.pyw to launch (no console window).
"""

import threading
import time
import json
import os
import sys
import pathlib
import winreg
import queue
import winsound
import subprocess
import ctypes

from PIL import Image, ImageDraw
import pystray
import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME         = "StandUpReminder"
APP_VERSION      = "1.0.0"
DEFAULT_INTERVAL = 40  # minutes

CONFIG_DIR       = pathlib.Path(os.environ["APPDATA"]) / APP_NAME
CONFIG_FILE      = CONFIG_DIR / "config.json"
STARTUP_FOLDER   = (
    pathlib.Path(os.environ["APPDATA"])
    / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
)
STARTUP_SHORTCUT = STARTUP_FOLDER / f"{APP_NAME}.lnk"

# ---------------------------------------------------------------------------
# Tray icon image generator
# ---------------------------------------------------------------------------

def _create_tray_icon(color: str, label: str = "S") -> Image.Image:
    """Create a 64x64 RGBA tray icon image (no external file needed)."""
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=color, outline="#ffffff", width=3)
    # Draw letter in center manually with a small rectangle approach
    # Use Pillow's built-in font (no external font file required)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    # Center the text
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
              label, fill="white", font=font)
    return img


ICON_RUNNING = _create_tray_icon("#2ecc71", "S")   # green
ICON_STOPPED = _create_tray_icon("#95a5a6", "S")   # grey
ICON_ALERT   = _create_tray_icon("#e74c3c", "!")   # red


# ---------------------------------------------------------------------------
# Config Manager
# ---------------------------------------------------------------------------

class ConfigManager:
    DEFAULTS = {
        "interval_minutes": DEFAULT_INTERVAL,
        "autostart":        True,
        "sound_enabled":    True,
    }

    def __init__(self):
        self._data = {}
        self.load()

    def load(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self._data = {**self.DEFAULTS, **json.load(f)}
            except Exception:
                self._data = dict(self.DEFAULTS)
        else:
            self._data = dict(self.DEFAULTS)
        self.save()

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key):
        return self._data.get(key, self.DEFAULTS.get(key))

    def set(self, key, value):
        self._data[key] = value
        self.save()


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

class StandUpReminder:
    """
    System tray stand-up reminder.

    Thread layout:
      Main thread  — pystray Win32 message pump (blocks in Icon.run())
      TkThread     — tkinter mainloop + queue polling (daemon)
      TimerThread  — threading.Timer fires _on_timer_fired (daemon, temporary)

    All UI work is dispatched to TkThread via self._ui_queue.
    _timer_lock protects _running and _timer.
    """

    def __init__(self):
        self.config              = ConfigManager()
        self._running            = False
        self._timer              = None
        self._timer_lock         = threading.Lock()
        self._ui_queue           = queue.Queue()
        self._tk_root            = None
        self._tray_icon          = None
        self._timer_start        = None   # monotonic time when current countdown began
        self._timer_interval_secs = 0     # interval in seconds for current countdown
        self._stop_tooltip_event = threading.Event()  # signals tooltip updater to stop

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self):
        self._start_tk_thread()
        self._build_tray_icon()
        if self.config.get("autostart"):
            # Register startup shortcut on first/every launch
            self._set_autostart(True)
            # Auto-start the timer
            self.start_timer()
        self._tray_icon.run()   # Blocks main thread (Win32 pump)

    def quit(self):
        self.stop_timer()
        self._ui_queue.put(("quit", None))
        if self._tray_icon:
            self._tray_icon.stop()

    # ------------------------------------------------------------------
    # Timer control
    # ------------------------------------------------------------------

    def start_timer(self):
        with self._timer_lock:
            if self._running:
                return
            self._running = True
        self._schedule_next()  # tooltip updater starts inside _schedule_next

    def stop_timer(self):
        self._stop_tooltip_event.set()
        with self._timer_lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
        self._timer_start = None
        self._update_tray(ICON_STOPPED, "Stand-Up Reminder — stopped")

    def restart_timer(self):
        """Called after popup is dismissed to reschedule next reminder."""
        with self._timer_lock:
            if not self._running:
                return
        self._schedule_next()  # tooltip updater starts inside _schedule_next

    def _schedule_next(self):
        interval_secs = self.config.get("interval_minutes") * 60
        self._timer_start         = time.monotonic()
        self._timer_interval_secs = interval_secs
        with self._timer_lock:
            self._timer = threading.Timer(interval_secs, self._on_timer_fired)
            self._timer.daemon = True
            self._timer.start()
        self._start_tooltip_updater()

    def _start_tooltip_updater(self):
        """Start (or restart) the 1-second tooltip countdown thread."""
        self._stop_tooltip_event.set()    # stop any existing updater
        self._stop_tooltip_event = threading.Event()
        stop = self._stop_tooltip_event

        def _updater():
            while not stop.is_set():
                if self._timer_start is None:
                    break
                elapsed  = time.monotonic() - self._timer_start
                remaining = max(0, self._timer_interval_secs - elapsed)
                mins, secs = divmod(int(remaining), 60)
                tooltip = f"Stand-Up Reminder — {mins:02d}:{secs:02d} remaining"
                self._update_tray(ICON_RUNNING, tooltip)
                stop.wait(1)

        t = threading.Thread(target=_updater, daemon=True, name="TooltipUpdater")
        t.start()

    def _on_timer_fired(self):
        """Runs on a temporary timer thread. Dispatch everything to correct threads."""
        self._stop_tooltip_event.set()   # stop countdown display while popup is open
        self._timer_start = None
        self._update_tray(ICON_ALERT, "Stand-Up Reminder — TIME TO STAND!")
        self._send_notification()
        self._ui_queue.put(("show_popup", self.config.get("interval_minutes")))

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _send_notification(self):
        """Send Windows toast notification via PowerShell (works on Win10/11)."""
        title   = "Time to Stand Up!"
        minutes = self.config.get("interval_minutes")
        message = f"You've been sitting for {minutes} minutes. Stand up and stretch!"

        ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.Visible = $true
$notify.ShowBalloonTip(8000, '{title}', '{message}', [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Milliseconds 8500
$notify.Dispose()
"""
        try:
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-NonInteractive", "-Command", ps_script],
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass  # Notification is secondary; popup is the primary alert

    # ------------------------------------------------------------------
    # Tkinter thread
    # ------------------------------------------------------------------

    def _start_tk_thread(self):
        t = threading.Thread(target=self._tk_thread_main, daemon=True, name="TkThread")
        t.start()

    def _tk_thread_main(self):
        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        self._tk_root.title(APP_NAME)
        self._poll_ui_queue()
        self._tk_root.mainloop()

    def _poll_ui_queue(self):
        try:
            while True:
                action, data = self._ui_queue.get_nowait()
                if action == "show_popup":
                    self._show_popup_dialog(data)
                elif action == "show_settings":
                    self._show_settings_dialog()
                elif action == "quit":
                    self._tk_root.destroy()
                    return
        except queue.Empty:
            pass
        self._tk_root.after(100, self._poll_ui_queue)

    def _show_popup_dialog(self, minutes: int):
        """Always-on-top popup reminder. Runs on TkThread."""
        # Start a looping beep on a daemon thread; stopped when popup is dismissed.
        stop_beep = threading.Event()

        def _beep_loop():
            while not stop_beep.is_set():
                try:
                    winsound.Beep(1000, 600)   # 1000 Hz for 600 ms
                    stop_beep.wait(0.1)        # short gap between beeps
                except Exception:
                    break

        if self.config.get("sound_enabled"):
            threading.Thread(target=_beep_loop, daemon=True, name="BeepThread").start()

        popup = tk.Toplevel(self._tk_root)
        popup.title("Stand-Up Reminder")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.protocol("WM_DELETE_WINDOW", lambda: None)  # Disable X button

        # Size and center
        w, h = 440, 240
        sw   = popup.winfo_screenwidth()
        sh   = popup.winfo_screenheight()
        popup.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        popup.configure(bg="#f0f0f0")

        frame = tk.Frame(popup, bg="#f0f0f0", padx=24, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        # Icon row — use ASCII art stand-up figure (avoids emoji encoding issues)
        icon_lbl = tk.Label(frame, text="[ STAND UP ]",
                            font=("Segoe UI", 14, "bold"), bg="#f0f0f0", fg="#e74c3c")
        icon_lbl.pack()

        # Main message
        tk.Label(
            frame,
            text=f"You've been sitting for {minutes} minutes!",
            font=("Segoe UI", 13, "bold"),
            bg="#f0f0f0",
            fg="#c0392b",
        ).pack(pady=(6, 2))

        tk.Label(
            frame,
            text="Please stand up, stretch, and rest your eyes.",
            font=("Segoe UI", 10),
            bg="#f0f0f0",
            fg="#555555",
        ).pack()

        def on_dismiss():
            stop_beep.set()   # Stops the beep loop
            popup.destroy()
            self._on_popup_dismissed()

        btn = tk.Button(
            frame,
            text="  OK - I'm Standing!  ",
            font=("Segoe UI", 11, "bold"),
            bg="#2ecc71",
            fg="white",
            activebackground="#27ae60",
            activeforeground="white",
            relief=tk.FLAT,
            cursor="hand2",
            command=on_dismiss,
            pady=8,
            padx=12,
        )
        btn.pack(pady=(18, 0))
        btn.focus_set()
        popup.bind("<Return>", lambda e: on_dismiss())

    def _on_popup_dismissed(self):
        """Called on TkThread after user clicks OK."""
        self.restart_timer()

    # ------------------------------------------------------------------
    # Settings dialog
    # ------------------------------------------------------------------

    def _show_settings_dialog(self):
        dlg = tk.Toplevel(self._tk_root)
        dlg.title(f"{APP_NAME} — Settings")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        dlg.configure(bg="#f0f0f0")

        w, h = 380, 260
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        frame = tk.Frame(dlg, bg="#f0f0f0", padx=24, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="Settings", font=("Segoe UI", 13, "bold"),
                 bg="#f0f0f0").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # Interval
        tk.Label(frame, text="Reminder interval (minutes):", font=("Segoe UI", 10),
                 bg="#f0f0f0").grid(row=1, column=0, sticky="w", pady=6)
        interval_var = tk.IntVar(value=self.config.get("interval_minutes"))
        tk.Spinbox(frame, from_=1, to=120, textvariable=interval_var,
                   width=6, font=("Segoe UI", 10)).grid(row=1, column=1, sticky="w", padx=10)

        # Sound
        sound_var = tk.BooleanVar(value=self.config.get("sound_enabled"))
        tk.Checkbutton(frame, text="Play sound on reminder", variable=sound_var,
                       font=("Segoe UI", 10), bg="#f0f0f0").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=4)

        # Autostart
        autostart_var = tk.BooleanVar(value=self.config.get("autostart"))
        tk.Checkbutton(frame, text="Start automatically with Windows",
                       variable=autostart_var, font=("Segoe UI", 10),
                       bg="#f0f0f0").grid(row=3, column=0, columnspan=2, sticky="w", pady=4)

        def on_save():
            new_interval   = max(1, min(120, int(interval_var.get())))
            new_autostart  = autostart_var.get()
            old_autostart  = self.config.get("autostart")
            self.config.set("interval_minutes", new_interval)
            self.config.set("sound_enabled", sound_var.get())
            self.config.set("autostart", new_autostart)
            if new_autostart != old_autostart:
                self._set_autostart(new_autostart)
            # Update tray tooltip
            if self._running:
                self._update_tray(ICON_RUNNING,
                    f"Stand-Up Reminder — running ({new_interval} min)")
            dlg.destroy()

        btn_frame = tk.Frame(frame, bg="#f0f0f0")
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(18, 0))
        tk.Button(btn_frame, text="Save", font=("Segoe UI", 10, "bold"),
                  bg="#2ecc71", fg="white", relief=tk.FLAT, padx=14, pady=5,
                  command=on_save).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frame, text="Cancel", font=("Segoe UI", 10),
                  bg="#bdc3c7", fg="#333", relief=tk.FLAT, padx=14, pady=5,
                  command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    # ------------------------------------------------------------------
    # System tray
    # ------------------------------------------------------------------

    def _build_tray_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem(
                "Start Timer",
                lambda icon, item: self.start_timer(),
                enabled=lambda item: not self._running,
            ),
            pystray.MenuItem(
                "Stop Timer",
                lambda icon, item: self.stop_timer(),
                enabled=lambda item: self._running,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Settings",
                lambda icon, item: self._ui_queue.put(("show_settings", None)),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Exit",
                lambda icon, item: self.quit(),
            ),
        )
        self._tray_icon = pystray.Icon(
            name=APP_NAME,
            icon=ICON_STOPPED,
            title="Stand-Up Reminder — stopped",
            menu=menu,
        )

    def _update_tray(self, image: Image.Image, tooltip: str):
        """Thread-safe tray update."""
        if self._tray_icon:
            self._tray_icon.icon  = image
            self._tray_icon.title = tooltip

    # ------------------------------------------------------------------
    # Windows startup registration
    # ------------------------------------------------------------------

    def _set_autostart(self, enable: bool):
        if getattr(sys, 'frozen', False):
            # Running as a PyInstaller .exe — self-contained, no Python interpreter needed
            target = pathlib.Path(sys.executable).resolve()
            args   = ""
        else:
            # Running as a .pyw script — need pythonw.exe + script path
            script_path = pathlib.Path(sys.argv[0]).resolve()
            pythonw     = pathlib.Path(sys.executable).parent / "pythonw.exe"
            if not pythonw.exists():
                pythonw = pathlib.Path(sys.executable)
            target = pythonw
            args   = f'"{script_path}"'

        if enable:
            self._create_startup_shortcut(target, args)
        else:
            self._remove_startup_shortcut()

    def _create_startup_shortcut(self, target: pathlib.Path, args: str):
        try:
            import win32com.client
            shell    = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(STARTUP_SHORTCUT))
            shortcut.TargetPath       = str(target)
            shortcut.Arguments        = args
            shortcut.WorkingDirectory = str(target.parent)
            shortcut.Description      = "Stand-Up Reminder — reminds you to stand up"
            shortcut.WindowStyle      = 7   # SW_SHOWMINNOACTIVE
            shortcut.Save()
        except Exception:
            # Registry fallback (HKCU — no admin needed)
            self._registry_set_autostart(True, target, args)

    def _remove_startup_shortcut(self):
        if STARTUP_SHORTCUT.exists():
            try:
                STARTUP_SHORTCUT.unlink()
            except Exception:
                pass
        self._registry_set_autostart(False)

    def _registry_set_autostart(self, enable: bool, target=None, args=None):
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path,
                                0, winreg.KEY_SET_VALUE) as key:
                if enable and target:
                    value = f'"{target}"' + (f' {args}' if args else "")
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, value)
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        except PermissionError:
            pass


# ---------------------------------------------------------------------------
# Single-instance guard
# ---------------------------------------------------------------------------

def _ensure_single_instance():
    """Return a mutex handle to keep alive for the process lifetime."""
    try:
        import win32event
        import win32api
        import winerror
        mutex = win32event.CreateMutex(None, False, f"Global\\{APP_NAME}Mutex")
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"{APP_NAME} is already running.\nCheck the system tray.",
                APP_NAME,
                0x40,  # MB_ICONINFORMATION
            )
            sys.exit(0)
        return mutex
    except ImportError:
        return None  # pywin32 not available; skip guard


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _mutex = _ensure_single_instance()
    app    = StandUpReminder()
    app.run()
