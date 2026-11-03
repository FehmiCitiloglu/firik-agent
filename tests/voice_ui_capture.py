"""Optional native-window captures of synthetic Tk fixtures, never the desktop."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys


def capture_window(window, destination):
    destination = Path(destination)
    if sys.platform != "darwin":
        # Captures are evidence for the macOS run, not a reason to capture an
        # overlapping user's desktop on platforms without this window API.
        return
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    pointer = ctypes.c_void_p
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cg.CGWindowListCopyWindowInfo.restype = pointer
    cf.CFArrayGetCount.argtypes = [pointer]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetValueAtIndex.argtypes = [pointer, ctypes.c_long]
    cf.CFArrayGetValueAtIndex.restype = pointer
    cf.CFDictionaryGetValue.argtypes = [pointer, pointer]
    cf.CFDictionaryGetValue.restype = pointer
    cf.CFNumberGetValue.argtypes = [pointer, ctypes.c_int, pointer]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [pointer]

    keys = {name: pointer.in_dll(cg, name).value for name in
            ("kCGWindowOwnerPID", "kCGWindowNumber", "kCGWindowName")}
    def number(info, name):
        value = cf.CFDictionaryGetValue(info, keys[name])
        result = ctypes.c_int64()
        return result.value if value and cf.CFNumberGetValue(value, 4, ctypes.byref(result)) else None

    windows = cg.CGWindowListCopyWindowInfo(1, 0)  # Onscreen windows only; no hidden Tk menus.
    if not windows:
        raise RuntimeError("Native test-window metadata is unavailable; refusing desktop capture.")
    candidates = []
    try:
        for index in range(cf.CFArrayGetCount(windows)):
            info = cf.CFArrayGetValueAtIndex(windows, index)
            if number(info, "kCGWindowOwnerPID") != os.getpid():
                continue
            title = cf.CFDictionaryGetValue(info, keys["kCGWindowName"])
            buffer = ctypes.create_string_buffer(1024)
            matched = bool(title and cf.CFStringGetCString(title, buffer, len(buffer), 0x08000100)
                           and buffer.value.decode("utf-8") == window.title())
            candidates.append((matched, number(info, "kCGWindowNumber")))
    finally:
        cf.CFRelease(windows)
    selected = [identifier for matched, identifier in candidates if matched]
    if not selected and len(candidates) == 1:
        selected = [candidates[0][1]]
    if len(selected) != 1 or not selected[0]:
        raise RuntimeError("Cannot identify the synthetic test window; refusing desktop capture.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/usr/sbin/screencapture", "-x", "-o", "-l", str(selected[0]), str(destination)],
                   check=True, capture_output=True, timeout=10)
