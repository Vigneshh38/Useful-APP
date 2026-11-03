"""SS Answer - right-click 5 times fast to screenshot the screen and get the answer.

Runs in the background. When you right-click 5 times within a couple of seconds,
it takes a screenshot of the monitor your mouse is on, sends it to Claude through
the Claude Code command (`claude`) using your Claude account login - no API key.
The answer is kept, and shown in a small always-on-top box only while you hold
Ctrl + Alt (it hides while the mouse is over it).

To answer from just part of the screen, left-click 5 times fast instead: a small
dot sticks to the mouse pointer. Left-click one corner of the area, then the
opposite corner - only the rectangle between the two dots is sent. Those two
clicks are not passed on to the app under the mouse. Press Esc to cancel.
"""

import base64
import ctypes
import io
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk

import mss
from PIL import Image
from pynput import keyboard, mouse

# ---- Settings ---------------------------------------------------------------
MODEL = os.environ.get("SS_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("SS_EFFORT", "high")   # low, medium, high, xhigh, max
CLAUDE_TIMEOUT_SECONDS = 120
CLICKS_NEEDED = 5
CLICK_WINDOW_SECONDS = 2.5   # all 5 right-clicks (or left-clicks) must happen within this time
REGION_COLOR = "#FF2D2D"     # colour of the corner dots (only if BLEND_WITH_BACKGROUND is off)
REGION_DOT_SIZE = 6          # dot size in pixels
MIN_REGION_SIZE = 10         # areas smaller than this (in pixels) are refused
BOX_ALPHA = 0.92             # box see-through level (1 = solid)
BLEND_WITH_BACKGROUND = True # box takes the colour of whatever is behind it
BLEND_CONTRAST = 60          # how far the text colour is from that colour (0-255, lower = fainter)
MAX_IMAGE_WIDTH = 1920       # screenshots are shrunk to this width to stay fast
DELETE_WINDOWS_SCREENSHOTS = True  # after an answer, delete the Windows screenshot (Win + PrtSc / Snipping Tool)...
RECENT_SCREENSHOT_SECONDS = 30     # ...saved up to this many seconds before the right-clicks, or while it was working

PROMPT = (
    "Analyze this chart and tell me if it will go up or down and describe it short and you must tell for sure will it go up or down based on your assumption. The answer you are providing is very big , make it short like within 4 to 5 lines . even if the chart is mock one or built by some demo , it should see the candles and let me know the same.add two point in starting like Short-Term: your answer ; Long-Term: Your answer"
)

# Make screen coordinates match real pixels on high-DPI (scaled) displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

CLAUDE_EXE = shutil.which("claude")
events = queue.Queue()  # messages from worker threads to the UI thread
busy = threading.Event()


WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004+: window is left out of screen recordings
GA_ROOT = 2


def hide_from_capture(win):
    """Keep a Tk window out of screenshots and screen recordings (Game Bar,
    Snipping Tool, OBS, etc.). It still shows normally on your own screen."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetAncestor(win.winfo_id(), GA_ROOT)
        return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x80000
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x8000000
LWA_ALPHA = 0x2


def make_click_through(win):
    """Let mouse clicks pass straight through a Tk window, and keep it from
    taking focus or showing in the taskbar."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetAncestor(win.winfo_id(), GA_ROOT)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
                              | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    except Exception:
        pass


def take_screenshot(region=None):
    """Grab the monitor the mouse is on (or just `region`) and return it as base64 JPEG."""
    x, y = (region["left"], region["top"]) if region else mouse.Controller().position
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        for m in sct.monitors[1:]:
            if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
                monitor = m
                break
        shot = sct.grab(region or monitor)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    if img.width > MAX_IMAGE_WIDTH:
        img = img.resize((MAX_IMAGE_WIDTH, round(img.height * MAX_IMAGE_WIDTH / img.width)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8"), monitor


def claude_env():
    """Environment for the claude command: drop API-key/proxy variables so it uses
    the Claude account you logged in with (`claude auth login`)."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(("ANTHROPIC_", "CLAUDE"))}


def ask_claude(image_b64):
    if not CLAUDE_EXE:
        return "Claude Code is not installed (the 'claude' command was not found)."
    message = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": PROMPT},
            ],
        },
    }
    cmd = [
        CLAUDE_EXE, "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json", "--verbose",
        "--model", MODEL,
        "--effort", EFFORT,
        "--tools", "",                    # no tools needed - just look and answer
        "--setting-sources", "project",   # skip your global settings (proxy/model overrides)
        "--strict-mcp-config",
        "--no-session-persistence",
    ]
    proc = subprocess.run(
        cmd,
        input=json.dumps(message) + "\n",
        capture_output=True, text=True, encoding="utf-8",
        timeout=CLAUDE_TIMEOUT_SECONDS,
        cwd=tempfile.gettempdir(),
        env=claude_env(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    result = None
    for line in proc.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") == "result":
            result = item
    if result is None:
        error = (proc.stderr or proc.stdout).strip() or f"claude exited with code {proc.returncode}"
        if "not logged in" in error.lower() or "/login" in error:
            return "Not logged in. Run:  claude auth login"
        return f"Error: {error[:300]}"
    text = (result.get("result") or "").strip()
    if "not logged in" in text.lower():
        return "Not logged in. Run:  claude auth login"
    return text or "No answer came back."


def windows_screenshots_folder():
    """The folder Windows saves Win + PrtSc and Snipping Tool screenshots to."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        folder, _ = winreg.QueryValueEx(key, "{B7BEDE81-DF94-4682-A7D8-57A52620B86F}")
        return os.path.expandvars(folder)
    except OSError:
        return os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")


def delete_recent_windows_screenshots(since, folder=None):
    """Delete Windows screenshots ("Screenshot ....png") created at or after `since`."""
    folder = folder or windows_screenshots_folder()
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        try:
            if (entry.is_file() and entry.name.lower().startswith("screenshot")
                    and entry.stat().st_ctime >= since):   # st_ctime = creation time on Windows
                os.remove(entry.path)
        except OSError:
            pass


def solve(region=None):
    started = time.time()
    try:
        if region and (region["width"] < MIN_REGION_SIZE or region["height"] < MIN_REGION_SIZE):
            events.put(("show", "That area is too small - pick two corners further apart.", None))
            return
        image_b64, monitor = take_screenshot(region)
        if region:
            events.put(("region_clear",))
        answer = ask_claude(image_b64)
        events.put(("show", answer, monitor))
    except subprocess.TimeoutExpired:
        events.put(("show", "Claude took too long - try again.", None))
    except Exception as e:
        events.put(("show", f"Error: {e}", None))
    finally:
        if region:
            events.put(("region_clear",))
        if DELETE_WINDOWS_SCREENSHOTS:
            delete_recent_windows_screenshots(started - RECENT_SCREENSHOT_SECONDS)
        busy.clear()


def start_solve(region=None):
    threading.Thread(target=solve, args=(region,), daemon=True).start()


def region_from_points(a, b):
    """The rectangle (in screen pixels) with corners at points a and b."""
    return {"left": min(a[0], b[0]), "top": min(a[1], b[1]),
            "width": abs(a[0] - b[0]), "height": abs(a[1] - b[1])}


click_times = {mouse.Button.right: [], mouse.Button.left: []}
region_active = threading.Event()  # set while picking an area with left-clicks
region_points = []
suppress_left_up = False
mouse_listener = None
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202


def start_region():
    region_points.clear()
    region_active.set()
    events.put(("region_start",))


def cancel_region():
    if region_active.is_set():
        region_active.clear()
        region_points.clear()
        events.put(("region_clear",))
        busy.clear()


def mouse_filter(msg, data):
    """While picking an area, each left-click places a corner dot and is kept
    away from the app under the mouse."""
    global suppress_left_up
    if msg == WM_LBUTTONDOWN and region_active.is_set():
        point = (data.pt.x, data.pt.y)
        region_points.append(point)
        events.put(("dot", *point))
        if len(region_points) == 2:
            region_active.clear()
            start_solve(region_from_points(*region_points))
        suppress_left_up = True
        mouse_listener.suppress_event()
    elif msg == WM_LBUTTONUP and suppress_left_up:
        suppress_left_up = False
        mouse_listener.suppress_event()
    return True


def on_click(x, y, button, pressed):
    if button not in click_times or not pressed:
        return
    times = click_times[button]
    now = time.monotonic()
    times.append(now)
    while times and now - times[0] > CLICK_WINDOW_SECONDS:
        times.pop(0)
    if len(times) >= CLICKS_NEEDED:
        times.clear()
        if not busy.is_set():
            busy.set()
            if button == mouse.Button.right:
                start_solve()
            else:
                start_region()


CTRL_KEYS = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
ALT_KEYS = {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr}
held_keys = set()


def on_key_press(key):
    held_keys.add(key)
    if key == keyboard.Key.esc:
        cancel_region()


def on_key_release(key):
    held_keys.discard(key)


def show_keys_held():
    return bool(held_keys & CTRL_KEYS) and bool(held_keys & ALT_KEYS)


def background_colors(sct, area):
    """The average colour of the screen in `area`, and a slightly lighter or
    darker one (by BLEND_CONTRAST) that is only just visible on it."""
    shot = sct.grab(area)
    r, g, b = Image.frombytes("RGB", shot.size, shot.rgb).resize((1, 1), Image.BOX).getpixel((0, 0))
    shift = BLEND_CONTRAST if 0.299 * r + 0.587 * g + 0.114 * b < 128 else -BLEND_CONTRAST
    bg = "#%02x%02x%02x" % (r, g, b)
    fg = "#%02x%02x%02x" % tuple(max(0, min(255, c + shift)) for c in (r, g, b))
    return bg, fg


class AnswerBox:
    """Keeps the latest answer and shows it only while Ctrl + Alt are held down
    and the mouse is not over the box."""

    def __init__(self, root):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)          # no title bar
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", BOX_ALPHA)
        self.win.configure(bg="#111111")
        self.label = tk.Label(
            self.win, text="", fg="#FFFFFF", bg="#111111",
            font=("Segoe UI", 13), justify="left", wraplength=460, padx=16, pady=12,
        )
        self.label.pack()
        self.has_answer = False
        self.shown = False
        self.pos = (0, 0)
        self.last_blend = 0.0
        self.sct = mss.mss()
        self.win.update_idletasks()
        hide_from_capture(self.win)
        self.win.withdraw()
        self.refresh()

    def refresh(self):
        want = self.has_answer and show_keys_held()
        if want and not self.shown:
            self.blend()
            self.win.deiconify()
            hide_from_capture(self.win)
            self.win.lift()
            self.win.attributes("-topmost", True)
            self.shown = True
        elif not want and self.shown:
            self.win.withdraw()
            self.shown = False
        if self.shown:
            # Fade out while the mouse is over the box, back in when it leaves.
            px, py = self.root.winfo_pointerxy()
            x, y = self.win.winfo_x(), self.win.winfo_y()
            over = x <= px < x + self.win.winfo_width() and y <= py < y + self.win.winfo_height()
            self.win.attributes("-alpha", 0.0 if over else BOX_ALPHA)
            if time.monotonic() - self.last_blend > 0.3:
                self.blend()
        self.root.after(30, self.refresh)

    def blend(self):
        """Colour the box like the screen behind it, with the text a little
        lighter or darker so it is only just readable."""
        if not BLEND_WITH_BACKGROUND:
            return
        self.last_blend = time.monotonic()
        try:
            x, y = self.pos
            # The box is left out of screen captures, so this sees what is behind it.
            bg, fg = background_colors(self.sct, {"left": x, "top": y, "width": self.win.winfo_reqwidth(),
                                                  "height": self.win.winfo_reqheight()})
        except Exception:
            return
        self.win.configure(bg=bg)
        self.label.config(bg=bg, fg=fg)

    def set_answer(self, text, monitor):
        self.label.config(text=text)
        self.win.update_idletasks()
        if monitor is None:
            left, top, width = 0, 0, self.root.winfo_screenwidth()
        else:
            left, top, width = monitor["left"], monitor["top"], monitor["width"]
        x = left + width - self.win.winfo_reqwidth() - 24
        y = top + 24
        self.win.geometry(f"+{x}+{y}")
        self.pos = (x, y)
        self.has_answer = True


class RegionPicker:
    """The small dot that follows the mouse pointer while you pick an area, and
    the corner dots you place. They let clicks through and are left out of
    screenshots."""

    def __init__(self, root):
        self.root = root
        self.cursor_dot = self.make_window()
        self.corner_dots = [self.make_window(), self.make_window()]
        self.placed = []
        self.active = False
        self.last_blend = {}
        self.sct = mss.mss()
        self.follow()

    def make_window(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=REGION_COLOR)
        win.geometry("1x1+0+0")
        win.update_idletasks()
        win.withdraw()
        return win

    def show(self, win, x, y, width, height):
        win.geometry(f"{max(1, width)}x{max(1, height)}+{x}+{y}")
        if win.state() == "withdrawn":
            win.deiconify()
            make_click_through(win)
            hide_from_capture(win)
            win.attributes("-topmost", True)
            self.last_blend.pop(win, None)
        if time.monotonic() - self.last_blend.get(win, 0.0) > 0.1:
            self.blend(win, x, y, width, height)

    def blend(self, win, x, y, width, height):
        """Colour a dot like the screen behind it, only a little
        lighter or darker so it is just visible."""
        if not BLEND_WITH_BACKGROUND:
            return
        self.last_blend[win] = time.monotonic()
        try:
            # The window is left out of screen captures, so this sees what is behind it.
            _, color = background_colors(self.sct, {"left": x, "top": y,
                                                    "width": max(1, width), "height": max(1, height)})
        except Exception:
            return
        win.configure(bg=color)

    def show_dot(self, win, x, y):
        half = REGION_DOT_SIZE // 2
        self.show(win, x - half, y - half, REGION_DOT_SIZE, REGION_DOT_SIZE)

    def start(self):
        self.clear()
        self.active = True

    def place(self, x, y):
        if len(self.placed) >= 2:
            return
        self.show_dot(self.corner_dots[len(self.placed)], x, y)
        self.placed.append((x, y))
        if len(self.placed) == 2:
            self.active = False
            self.cursor_dot.withdraw()

    def clear(self):
        self.active = False
        self.placed = []
        for win in [self.cursor_dot, *self.corner_dots]:
            win.withdraw()

    def follow(self):
        if self.active:
            self.show_dot(self.cursor_dot, *self.root.winfo_pointerxy())
        self.root.after(15, self.follow)


def already_running():
    """True if another copy of the app is already running (one copy at a time)."""
    global _instance_mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _instance_mutex = kernel32.CreateMutexW(None, False, "SSAnswerSingleInstance")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def main():
    if already_running():
        print("SS Answer is already running.")
        return
    root = tk.Tk()
    root.withdraw()
    box = AnswerBox(root)
    picker = RegionPicker(root)

    def poll():
        while not events.empty():
            kind, *args = events.get()
            if kind == "show":
                box.set_answer(*args)
            elif kind == "region_start":
                picker.start()
            elif kind == "dot":
                picker.place(*args)
            elif kind == "region_clear":
                picker.clear()
        root.after(30, poll)

    global mouse_listener
    mouse_listener = mouse.Listener(on_click=on_click, win32_event_filter=mouse_filter)
    mouse_listener.start()
    key_listener = keyboard.Listener(on_press=on_key_press, on_release=on_key_release)
    key_listener.start()
    print(f"SS Answer is running (model: {MODEL}, effort: {EFFORT}). Right-click {CLICKS_NEEDED} times fast to get an answer.")
    print(f"Left-click {CLICKS_NEEDED} times fast, then click two corners to answer from just that area (Esc cancels).")
    print("Hold Ctrl + Alt to see the latest answer.")
    print("Close this window or press Ctrl+C to stop.")
    poll()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    mouse_listener.stop()
    key_listener.stop()


if __name__ == "__main__":
    main()
