"""Не отдавать фокус экрана окну Chrome на X11.

Chromedriver при новой вкладке поднимает окно поверх. Сторож возвращает
фокус предыдущему окну и опускает Chrome вниз стека, не сворачивая его:
свёрнутое окно перестаёт нормально рисовать страницу.
"""

from __future__ import annotations

import ctypes
import threading
import time
from pathlib import Path

from loguru import logger

_ClientMessage = 33
_SubstructureMask = (1 << 19) | (1 << 20)


class _XClientMessage(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [
        ("xclient", _XClientMessage),
        ("pad", ctypes.c_byte * 256),
    ]


def _libx11():
    lib = ctypes.CDLL("libX11.so.6")
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XFree.argtypes = [ctypes.c_void_p]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XLowerWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XSendEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_long,
        ctypes.POINTER(_XEvent),
    ]
    lib.XGetWindowProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    return lib


def _window_prop(lib, display, window: int, atom: int) -> int | None:
    actual_type = ctypes.c_ulong()
    actual_format = ctypes.c_int()
    nitems = ctypes.c_ulong()
    bytes_after = ctypes.c_ulong()
    prop = ctypes.c_void_p()
    status = lib.XGetWindowProperty(
        display,
        ctypes.c_ulong(window),
        ctypes.c_ulong(atom),
        0,
        1,
        0,
        0,
        ctypes.byref(actual_type),
        ctypes.byref(actual_format),
        ctypes.byref(nitems),
        ctypes.byref(bytes_after),
        ctypes.byref(prop),
    )
    if status != 0 or not prop or nitems.value < 1:
        if prop:
            lib.XFree(prop)
        return None
    if actual_format.value == 32:
        value = ctypes.cast(prop, ctypes.POINTER(ctypes.c_ulong))[0]
    else:
        value = ctypes.cast(prop, ctypes.POINTER(ctypes.c_uint32))[0]
    lib.XFree(prop)
    return int(value)


def descendant_pids(root: int) -> set[int]:
    """root и все процессы, которые от него запущены."""
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            text = stat.read_text()
            rparen = text.rfind(")")
            parts = text[rparen + 2 :].split()
            pid = int(text.split(" ", 1)[0])
            ppid = int(parts[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(pid)
    found = {root}
    stack = [root]
    while stack:
        current = stack.pop()
        for child in children.get(current, ()):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


class FocusGuard:
    """Держит чужие окна поверх Chrome, пока жив драйвер."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._root_pid = 0

    def start(self, root_pid: int) -> None:
        self._root_pid = root_pid
        if (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop.is_set()
        ):
            return
        if self._thread is not None and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=1.0)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="chrome-focus-guard", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        lib = _libx11()
        display = lib.XOpenDisplay(None)
        if not display:
            logger.warning("Фокус Chrome не удерживаю: нет X-дисплея")
            return
        try:
            root = lib.XDefaultRootWindow(display)
            active_atom = lib.XInternAtom(display, b"_NET_ACTIVE_WINDOW", 0)
            pid_atom = lib.XInternAtom(display, b"_NET_WM_PID", 0)
            user_window = _window_prop(lib, display, root, active_atom) or 0
            pids = descendant_pids(self._root_pid)
            refreshed = time.monotonic()
            while not self._stop.is_set():
                now = time.monotonic()
                if now - refreshed > 1.0:
                    pids = descendant_pids(self._root_pid)
                    refreshed = now
                active = _window_prop(lib, display, root, active_atom) or 0
                if not active:
                    time.sleep(0.05)
                    continue
                active_pid = _window_prop(lib, display, active, pid_atom)
                if active_pid in pids:
                    lib.XLowerWindow(display, ctypes.c_ulong(active))
                    if user_window and user_window != active:
                        _activate(lib, display, root, active_atom, user_window)
                    lib.XFlush(display)
                else:
                    user_window = active
                time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Сторож фокуса Chrome остановился: {}", exc)
        finally:
            lib.XCloseDisplay(display)


def _activate(lib, display, root: int, atom: int, window: int) -> None:
    event = _XEvent()
    event.xclient.type = _ClientMessage
    event.xclient.display = display
    event.xclient.window = window
    event.xclient.message_type = atom
    event.xclient.format = 32
    event.xclient.data[0] = 2
    event.xclient.data[1] = 0
    event.xclient.data[2] = 0
    lib.XSendEvent(
        display,
        ctypes.c_ulong(root),
        0,
        _SubstructureMask,
        ctypes.byref(event),
    )


_guard = FocusGuard()


def hold_chrome_in_background(root_pid: int) -> None:
    _guard.start(root_pid)


def release_chrome_focus() -> None:
    _guard.stop()
