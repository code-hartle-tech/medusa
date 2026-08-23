#!/usr/bin/env python3
"""Read an ESP's USB-CDC serial for a bounded number of seconds (no pyserial).

Robust to a reset of the same requested device path: keeps reopening that exact
path whenever it is silent for >2s. It never falls back to another serial port;
if USB re-enumeration changes the path, detection must be run again.
"""
import os, select, sys, time

if len(sys.argv) < 2:
    print("usage: read_serial.py <exact-port> [seconds]", file=sys.stderr)
    sys.exit(2)

req_port = sys.argv[1]
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0
deadline = time.time() + secs

def resolve():
    return req_port if req_port and os.path.exists(req_port) else None

def try_open():
    p = resolve()
    if not p:
        return None
    try:
        return os.open(p, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None

fd = None
opened_requested_port = False
last_data = time.time()
while time.time() < deadline:
    if fd is None:
        fd = try_open()
        if fd is None:
            time.sleep(0.4); continue
        opened_requested_port = True
        last_data = time.time()
    r, _, _ = select.select([fd], [], [], 0.5)
    if r:
        try:
            d = os.read(fd, 4096)
        except OSError:
            d = b""
        if d:
            last_data = time.time()
            sys.stdout.write(d.decode("utf-8", "replace")); sys.stdout.flush()
        else:
            os.close(fd); fd = None; time.sleep(0.3); continue
    if fd is not None and time.time() - last_data > 2.0:
        try: os.close(fd)
        except OSError: pass
        fd = None   # stale fd after re-enumeration -> reopen (re-resolve name)
if fd is not None:
    try: os.close(fd)
    except OSError: pass
sys.exit(0 if opened_requested_port else 2)
