"""Isolated Windows worker for the locally supplied wx_key DLL."""

import ctypes
import json
import re
import sys
import time


def emit(stage, **fields):
    print(json.dumps({"stage": stage, **fields}), flush=True)


def main():
    dll_path, pid = sys.argv[1:]
    try:
        dll = ctypes.CDLL(dll_path)
        initialize = dll.InitializeHook
        initialize.argtypes = [ctypes.c_uint32]
        initialize.restype = ctypes.c_bool
        poll = dll.PollKeyData
        poll.argtypes = [ctypes.POINTER(ctypes.c_char), ctypes.c_int32]
        poll.restype = ctypes.c_bool
        cleanup = dll.CleanupHook
        cleanup.argtypes = []
        cleanup.restype = ctypes.c_bool
    except (OSError, AttributeError):
        emit("dll_failed")
        return
    emit("dll_loaded")
    if not initialize(int(pid)):
        emit("hook_failed")
        return
    emit("hook_initialized")
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            buffer = ctypes.create_string_buffer(65)
            if poll(buffer, len(buffer)):
                key = buffer.value.decode("ascii", errors="ignore").strip()
                if re.fullmatch(r"[0-9a-fA-F]{64}", key):
                    emit("key", key=key)
                    return
            time.sleep(0.2)
        emit("timeout")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
