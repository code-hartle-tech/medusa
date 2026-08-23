#!/usr/bin/env python3
"""
esp-hw — Medusa's real hardware bridge (Xtensa + RISC-V).

Discover a supported ESP over USB, build stock receive-only firmware, and flash
an operator-confirmed board. A separately gated research path can build against
an isolated copy of Espressif's Wi-Fi library for defensive driver study.

The implementation constants below are required for reviewable, deterministic
build behavior. See wiki/research/marauder-deauth-patch.md for the public
defensive summary; the full teardown and operator procedures live in the
tailnet-only internal wiki.

The patch is per-architecture:
  Xtensa (ESP32 / S3):  movi.n a2,0 ; retw.n   ->  0c 02 1d f0
  RISC-V (ESP32-C3):    c.li a0,0  ; c.jr ra   ->  01 45 82 80
Both are computed/verified from the real blob — nothing hardcoded blindly.

Subcommands:  scan | patch | build | flash | unattended-dump
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile

HOME = os.path.expanduser("~")
ARDUINO15 = os.path.join(HOME, "Library", "Arduino15")
GATE_SYMBOL = "ieee80211_raw_frame_sanity_check"
GATE_MEMBER = "ieee80211_output.o"

# chip (as esptool reports it, normalised lower/hyphen) -> build recipe
ARCHES = {
    "esp32":    dict(prefix="xtensa-esp32-elf",   bytes=bytes([0x0C, 0x02, 0x1D, 0xF0]),
                     verify=("movi.n", "retw.n"), fqbn="esp32:esp32:esp32",                    target="esp32"),
    # S3 support comes from the m5stack core here (the stock esp32:esp32 core is
    # 1.0.0 = classic-ESP32 only). The gate patch is identical Xtensa bytes.
    "esp32-s3": dict(prefix="xtensa-esp32s3-elf", bytes=bytes([0x0C, 0x02, 0x1D, 0xF0]),
                     verify=("movi.n", "retw.n"), fqbn="m5stack:esp32:m5stack_stamp_s3",       target="esp32s3"),
    "esp32-c3": dict(prefix="riscv32-esp-elf",    bytes=bytes([0x01, 0x45, 0x82, 0x80]),
                     verify=("li", "ret"),        fqbn="m5stack:esp32:m5stack_stamp_c3:CDCOnBoot=cdc", target="esp32c3"),
    # RISC-V siblings — same gate patch as the C3; offsets are auto-computed per blob.
    # C6 adds Wi-Fi 6 + 802.15.4 (Zigbee/Thread); C5 adds dual-band 5 GHz. Both need
    # a recent arduino-esp32 core (3.x) installed for the fqbn to resolve.
    "esp32-c6": dict(prefix="riscv32-esp-elf",    bytes=bytes([0x01, 0x45, 0x82, 0x80]),
                     verify=("li", "ret"),        fqbn="esp32:esp32:esp32c6",                     target="esp32c6"),
    "esp32-c5": dict(prefix="riscv32-esp-elf",    bytes=bytes([0x01, 0x45, 0x82, 0x80]),
                     verify=("li", "ret"),        fqbn="esp32:esp32:esp32c5",                     target="esp32c5"),
    # NOT ARCHES entries — different injection path / toolchain, tracked as separate lanes:
    #   esp8266  → no libnet80211 gate; raw TX via wifi_send_pkt_freedom (community core)
    #   rp2040/rp2350 → no native Wi-Fi (CYW43439 on -W boards, injection-limited); arduino-pico
    #                   toolchain. Best used for BadUSB/HID + external-radio host, not Wi-Fi TX.
}

C = dict(g="\033[32m", y="\033[33m", r="\033[31m", b="\033[34m", d="\033[2m", x="\033[0m")
def say(msg, c="x"): print(f"{C.get(c,'')}{msg}{C['x']}")


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def find_tool(prefix, name):
    hits = glob.glob(os.path.join(ARDUINO15, "packages", "*", "tools",
                                  f"{prefix}-gcc", "*", "bin", f"{prefix}-{name}"))
    return hits[0] if hits else shutil.which(f"{prefix}-{name}")

def find_esptool():
    return shutil.which("esptool") or shutil.which("esptool.py")

def resolve_port(port):
    """Return only the exact requested port; never substitute another board.

    USB re-enumeration may briefly remove a path, but silently selecting the
    sole remaining serial device can flash or read an unrelated board. Callers
    must fail closed and run detection again if the requested path changes.
    """
    return port if port and os.path.exists(port) else None

def find_esp32_core_libs():
    return sorted(glob.glob(os.path.join(
        ARDUINO15, "packages", "*", "hardware", "esp32", "*", "**", "libnet80211.a"), recursive=True))

def find_core_lib(target):
    """Pick the libnet80211.a for a given chip target (esp32 / esp32s3 / esp32c3)."""
    libs = find_esp32_core_libs()
    others = ("esp32s3", "esp32s2", "esp32c3", "esp32c6", "esp32c5", "esp32h2")
    if target == "esp32":
        for l in libs:
            if not any(f"/{o}/" in l for o in others):
                return l
    else:
        for l in libs:
            if f"/{target}/" in l:
                return l
    return None

def normalize_chip(s):
    return s.strip().lower().replace(" ", "-").replace("(", "").replace(")", "")

def detect_chip(port, timeout=20):
    """Ask the modern esptool what chip is on the port. Returns e.g. 'esp32-c3'."""
    esptool = find_esptool()
    if not esptool:
        return None
    try:
        r = subprocess.run([esptool, "--port", port, "--connect-attempts", "2", "flash-id"],
                           capture_output=True, text=True, timeout=timeout)
        blob = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"Detecting chip type\.\.\.\s*([A-Za-z0-9\-]+)", blob) or \
            re.search(r"Chip is (ESP32[\w\-]*)", blob)
        if m:
            return normalize_chip(m.group(1))
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
USB_HINT = re.compile(r"(usbserial|usbmodem|wchusbserial|SLAB_USBtoUART|usbto|UART)", re.I)
BT_HINT  = re.compile(r"(Bluetooth|debug-console|Buds|soundcore|Hi-X|Airpods|SpaceOne)", re.I)

def probe_esp(port, timeout=15):
    esptool = find_esptool()
    out = {"chip": None, "mac": None, "detected": False, "probe": "no esptool"}
    if not esptool:
        return out
    try:
        r = subprocess.run([esptool, "--port", port, "--connect-attempts", "2", "flash-id"],
                           capture_output=True, text=True, timeout=timeout)
        blob = (r.stdout or "") + (r.stderr or "")
        mchip = re.search(r"Detecting chip type\.\.\.\s*([A-Za-z0-9\-]+)", blob) or re.search(r"Chip is (ESP32[\w\-]*)", blob)
        mmac  = re.search(r"MAC:\s*([0-9a-fA-F:]{17})", blob)
        if mchip or mmac:
            out.update(chip=mchip.group(1) if mchip else None,
                       mac=mmac.group(1) if mmac else None, detected=True, probe="ok")
    except subprocess.TimeoutExpired:
        out["probe"] = "timeout (hold BOOT / check the board)"
    except Exception as e:
        out["probe"] = f"error: {e}"
    return out

def scan_usb(probe=False, timeout=15, port=None):
    ports = []
    devices = [port] if port and os.path.exists(port) else (
        [] if port else sorted(
            glob.glob("/dev/cu.*")
            + glob.glob("/dev/ttyUSB*")
            + glob.glob("/dev/ttyACM*")
        )
    )
    for dev in devices:
        base = os.path.basename(dev)
        if BT_HINT.search(base):
            continue
        looks = bool(USB_HINT.search(base))
        entry = {"port": dev, "looks_like_esp_bridge": looks, "chip": None, "mac": None, "detected": False}
        if probe and (looks or port is not None):
            entry.update(probe_esp(dev, timeout))
        ports.append(entry)
    return ports

def scan_wifi(timeout=4):
    if not shutil.which("dns-sd"):
        return {"services": [], "note": "dns-sd unavailable"}
    found = []
    for svc in ("_arduino._tcp", "_esp._tcp", "_espota._tcp"):
        try:
            p = subprocess.Popen(["dns-sd", "-B", svc, "local."], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True)
            try:
                out, _ = p.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.terminate()
                try: out, _ = p.communicate(timeout=2)
                except subprocess.TimeoutExpired: p.kill(); out, _ = p.communicate()
            for line in (out or "").splitlines():
                if re.search(r"\bAdd\b", line):
                    name = line.split()[-1]
                    if name and name not in ("Name", "Instance"):
                        found.append({"service": svc, "name": name})
        except Exception:
            pass
    seen, uniq = set(), []
    for f in found:
        k = (f["service"], f["name"])
        if k not in seen: seen.add(k); uniq.append(f)
    return {"services": uniq}

def cmd_scan(a):
    result = {}
    if a.usb or not a.wifi:
        result["usb"] = scan_usb(
            probe=a.probe,
            timeout=a.timeout if a.timeout > 8 else 15,
            port=getattr(a, "port", None),
        )
    if a.wifi or not a.usb:
        result["wifi"] = scan_wifi(timeout=a.timeout)
    if a.json:
        print(json.dumps(result, indent=2)); return 0
    say("== USB / serial ==", "b")
    for e in result.get("usb", []) or []:
        tag = "ESP bridge" if e["looks_like_esp_bridge"] else "serial"
        if e.get("detected"):
            det = f"  {C['g']}-> {e.get('chip') or 'ESP'}  MAC {e.get('mac')}{C['x']}"
        else:
            det = f"  {C['d']}({e.get('probe','')}){C['x']}" if e.get("probe") else ""
        print(f"  {e['port']}  [{tag}]{det}")
    if not result.get("usb"):
        say("  (no USB serial ports — plug an ESP in over USB)", "d")
    if "wifi" in result:
        say("== WiFi / mDNS (ArduinoOTA/OTA) ==", "b")
        for s in result["wifi"].get("services", []):
            print(f"  {s['name']}  [{s['service']}]")
        if not result["wifi"].get("services"):
            say("  (no OTA-advertising ESPs on the network right now)", "d")
    return 0


# --------------------------------------------------------------------------- #
# patch
# --------------------------------------------------------------------------- #
def _gate_file_offset(objdump, nm, member_path, symbol):
    sym_val = None
    for line in subprocess.run([nm, member_path], capture_output=True, text=True).stdout.splitlines():
        p = line.split()
        if len(p) >= 3 and p[-1] == symbol:
            sym_val = int(p[0], 16)
    if sym_val is None:
        raise RuntimeError(f"symbol {symbol} not found in {member_path}")
    sec_off = None
    for line in subprocess.run([objdump, "-h", member_path], capture_output=True, text=True).stdout.splitlines():
        if f".text.{symbol}" in line:
            sec_off = int(line.split()[5], 16)
    if sec_off is None:
        raise RuntimeError(f"section .text.{symbol} not found in {member_path}")
    return sec_off + sym_val

def arch_for_lib(path):
    """Infer architecture recipe from a blob path (esp32c3 -> riscv, else xtensa)."""
    if any(k in path for k in ("esp32c3", "esp32c6", "esp32c5", "esp32h2")):
        return ARCHES["esp32-c3"]
    if "esp32s3" in path:
        return ARCHES["esp32-s3"]
    return ARCHES["esp32"]

def patch_lib(src, out, arch, symbol=GATE_SYMBOL, member=GATE_MEMBER, verbose=True):
    """Copy src libnet80211.a -> out, patch the gate to return 0 for the given arch,
    verify by disassembly. Returns dict(ok, offset, before, after, disasm, out)."""
    objdump = find_tool(arch["prefix"], "objdump")
    nm      = find_tool(arch["prefix"], "nm")
    ar      = find_tool(arch["prefix"], "ar") or shutil.which("ar")
    if not (objdump and nm and ar):
        raise RuntimeError(f"{arch['prefix']} toolchain not found (install the matching esp32 core)")
    if not os.path.isfile(src):
        raise RuntimeError(f"no such file: {src}")
    shutil.copyfile(src, out)
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([ar, "x", os.path.abspath(out), member], cwd=td, check=True)
        mpath = os.path.join(td, member)
        off = _gate_file_offset(objdump, nm, mpath, symbol)
        with open(mpath, "r+b") as f:
            f.seek(off); before = f.read(len(arch["bytes"]))
            f.seek(off); f.write(arch["bytes"])
        subprocess.run([ar, "r", os.path.abspath(out), member], cwd=td, check=True)
    with tempfile.TemporaryDirectory() as vd:
        subprocess.run([ar, "x", os.path.abspath(out), member], cwd=vd, check=True)
        vm = os.path.join(vd, member)
        d = subprocess.run([objdump, "-d", "-j", f".text.{symbol}", vm], capture_output=True, text=True).stdout
        body = d.split(f"<{symbol}>:", 1)[-1]
        first = [l.strip() for l in body.splitlines() if "\t" in l][:2]
        joined = " ".join(first)
        ok = all(tok in joined for tok in arch["verify"])
    if verbose:
        say(f"gate '{symbol}' @ file offset 0x{off:x} in {member}  [{arch['prefix']}]", "b")
        print(f"  before: {before.hex(' ')}")
        print(f"  after : {arch['bytes'].hex(' ')}")
        say("  verify (disasm of patched gate):", "b")
        for l in first: print("   ", l)
    return dict(ok=ok, offset=off, before=before, after=arch["bytes"], disasm=first, out=out)

def cmd_patch(a):
    src = a.libnet80211a
    if not src:
        src = find_core_lib("esp32")
        if not src:
            say("no esp32 core libnet80211.a found under ~/Library/Arduino15", "r"); return 2
        say(f"auto-selected core blob: {src}", "d")
    arch = ARCHES.get(a.chip) if a.chip else arch_for_lib(src)
    try:
        res = patch_lib(src, a.out or (src + ".patched"), arch, verbose=True)
    except Exception as e:
        say(str(e), "r"); return 2
    if res["ok"]:
        say(f"OK  patched blob written: {res['out']}", "g"); return 0
    say("VERIFY FAILED — patched gate disasm did not match expected return-0", "r"); return 1


# --------------------------------------------------------------------------- #
def cmd_build(a):
    from esp_build import build
    return build(a)

def cmd_flash(a):
    from esp_build import flash
    return flash(a)

def cmd_scan_aps(a):
    from esp_build import scan_aps
    return scan_aps(a)

def cmd_recon(a):
    from esp_build import recon
    return recon(a)

def cmd_scan_clients(a):
    from esp_build import scan_clients
    return scan_clients(a)

def cmd_sniff(a):
    from esp_build import sniff
    return sniff(a)

def cmd_capture_wpa(a):
    from esp_build import capture_wpa
    return capture_wpa(a)

def cmd_ble_scan(a):
    from esp_build import ble_scan
    return ble_scan(a)

def cmd_monitor(a):
    from esp_build import monitor
    return monitor(a)

def cmd_congestion(a):
    from esp_build import congestion
    return congestion(a)

def cmd_vitals(a):
    from esp_build import vitals
    return vitals(a)

def cmd_csi_raw(a):
    from esp_build import csi_raw
    return csi_raw(a)

def cmd_csi(a):
    from esp_build import csi
    return csi(a)

def cmd_ftm(a):
    from esp_build import ftm
    return ftm(a)


def cmd_unattended_dump(a):
    """Flush and retrieve the passive unattended inventory over local USB."""
    from unattended_dump import UnattendedDumpError, retrieve_to_file
    port = resolve_port(a.port)
    if not port or not os.path.exists(port):
        say(f"port not found: {a.port} — reconnect the unattended board over USB.", "r")
        return 2
    output = os.path.abspath(a.out)
    try:
        result = retrieve_to_file(port, output, timeout=a.timeout)
    except (UnattendedDumpError, ValueError) as exc:
        say(f"unattended retrieval failed: {exc}", "r")
        return 1
    payload = {
        "path": output,
        "rows": result.row_count,
        "partial": result.partial,
        "capacityDrops": result.capacity_drops,
        "recovery": result.recovery,
        "source": result.source,
    }
    print("UNATTENDED_DUMP_JSON " + json.dumps(payload, separators=(",", ":")))
    suffix = f" (PARTIAL: {result.capacity_drops} observations dropped after capacity was reached)" if result.partial else ""
    if result.recovery == "database-read-only":
        recovery = " (RECOVERY: rendered from the validated database read-only; readback did not clear recovery artifacts)"
    elif result.recovery == "volatile-ram-read-only":
        recovery = " (RECOVERY: rendered from non-persisted volatile RAM; readback did not clear recovery artifacts)"
    elif result.recovery == "orphaned-csv-preserved":
        recovery = f" (RECOVERY: source-labelled preserved CSV read-only from {result.source}; readback did not clear recovery artifacts)"
    else:
        recovery = ""
    say(f"saved {result.row_count} inventory rows to {output}{suffix}{recovery}", "g")
    return 0


def main():
    p = argparse.ArgumentParser(prog="esp-hw", description="Medusa hardware bridge (Xtensa + RISC-V).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan"); s.add_argument("--usb", action="store_true"); s.add_argument("--wifi", action="store_true")
    s.add_argument("--port", help="probe only this exact serial path")
    s.add_argument("--json", action="store_true"); s.add_argument("--probe", action="store_true")
    s.add_argument("--timeout", type=float, default=6.0); s.set_defaults(func=cmd_scan)

    pt = sub.add_parser("patch"); pt.add_argument("libnet80211a", nargs="?")
    pt.add_argument("--out"); pt.add_argument("--chip", choices=list(ARCHES)); pt.set_defaults(func=cmd_patch)

    b = sub.add_parser("build", help="compile firmware only; build success is not runtime or RF proof"); b.add_argument("--chip", choices=list(ARCHES)); b.add_argument("--port")
    b.add_argument("--sketch"); b.add_argument("--out"); b.add_argument("--fqbn")
    b.add_argument("--no-patch", action="store_true", help="build STOCK (unpatched) firmware")
    b.add_argument("--bssid", help="authorized lab AP BSSID (research firmware build only)")
    b.add_argument("--channel", type=int, help="authorized lab channel 1-14")
    b.add_argument("--client", help="authorized lab client STA MAC for a scoped resilience build")
    b.add_argument("--attack", choices=["deauth", "csa", "authflood", "beaconspam", "probeflood", "blespam", "badusb", "unattended"], default="deauth",
                   help="firmware variant (build only; active research use remains separately gated and authorized)")
    b.add_argument("--ssid", help="target SSID (used by the CSA beacon or directed probe test)")
    b.add_argument("--payload", help="BadUSB keystroke payload (S3 only)")
    b.set_defaults(func=cmd_build)

    f = sub.add_parser("flash", help="write a prebuilt image to the exact requested port; not runtime proof"); f.add_argument("--port", required=True); f.add_argument("--chip", choices=list(ARCHES))
    f.add_argument("--bin"); f.add_argument("--build-dir"); f.add_argument("--fqbn"); f.add_argument("--baud", default="460800")
    f.set_defaults(func=cmd_flash)

    sa = sub.add_parser("scan-aps", help="flash a scan firmware and list nearby 2.4GHz APs")
    sa.add_argument("--port", required=True); sa.add_argument("--chip", choices=list(ARCHES))
    sa.set_defaults(func=cmd_scan_aps)

    rc = sub.add_parser("recon", help="profile advertised AP security configuration (RSN/PMF/WPS)")
    rc.add_argument("--port", required=True); rc.add_argument("--chip", choices=list(ARCHES))
    rc.add_argument("--channel", type=int, help="dwell on one channel (else sweep 1-13)")
    rc.set_defaults(func=cmd_recon)

    scq = sub.add_parser("scan-clients", help="inventory stations observed on an authorized lab AP")
    scq.add_argument("--port", required=True); scq.add_argument("--chip", choices=list(ARCHES))
    scq.add_argument("--bssid", required=True); scq.add_argument("--channel", type=int, required=True)
    scq.set_defaults(func=cmd_scan_clients)

    sn = sub.add_parser("sniff", help="passive capture on a channel → PCAP (mgmt + EAPOL)")
    sn.add_argument("--port", required=True); sn.add_argument("--chip", choices=list(ARCHES))
    sn.add_argument("--channel", type=int, required=True, help="channel to pin 1-14")
    sn.add_argument("--seconds", type=int, default=20, help="capture window seconds")
    sn.set_defaults(func=cmd_sniff)

    mo = sub.add_parser("monitor", help="passive traffic monitor + signal meter on a channel")
    mo.add_argument("--port", required=True); mo.add_argument("--chip", choices=list(ARCHES))
    mo.add_argument("--channel", type=int, required=True); mo.add_argument("--bssid", help="track this BSSID's RSSI")
    mo.add_argument("--seconds", type=int, default=12)
    mo.set_defaults(func=cmd_monitor)

    cg = sub.add_parser("congestion", help="passive per-channel congestion survey with overlap-aware ranking")
    cg.add_argument("--port", required=True); cg.add_argument("--chip", choices=list(ARCHES))
    cg.add_argument("--sweeps", type=int, default=3, help="passes over the band")
    cg.add_argument("--dwell", type=int, default=1200, help="ms per channel per pass")
    cg.set_defaults(func=cmd_congestion)

    cr = sub.add_parser("csi-raw", help="export unprocessed per-frame CSI (I/Q, phase preserved)")
    cr.add_argument("--port", required=True); cr.add_argument("--chip", choices=list(ARCHES))
    cr.add_argument("--channel", type=int, required=True)
    cr.add_argument("--bssid", help="restrict to one transmitter (strongly recommended)")
    cr.add_argument("--seconds", type=int, default=60)
    cr.add_argument("--save", help="where to write the raw stream")
    cr.add_argument("--ssid", help="ACTIVE MODE: join this network and ping it, instead of "
                        "listening passively. The only way to control the sample rate.")
    cr.add_argument("--password", help="password for --ssid (never logged or committed)")
    cr.add_argument("--ping-ms", type=int, default=25, help="echo interval in active mode")
    cr.set_defaults(func=cmd_csi_raw)

    vt = sub.add_parser("vitals", help="capture raw CSI and estimate respiration rate")
    vt.add_argument("--port", required=True); vt.add_argument("--chip", choices=list(ARCHES))
    vt.add_argument("--channel", type=int, required=True)
    vt.add_argument("--bssid", help="transmitter to sense against (must be a BUSY one)")
    vt.add_argument("--seconds", type=int, default=90)
    vt.add_argument("--save", required=True, help="where to keep the raw capture for re-analysis")
    vt.add_argument("--signal", choices=["amplitude", "phase-diff"], default="amplitude")
    vt.add_argument("--min-bpm", type=float, default=6.0)
    vt.add_argument("--max-bpm", type=float, default=36.0)
    vt.add_argument("--expect", type=float, help="ground-truth bpm; prints the error")
    vt.add_argument("--ssid", help="ACTIVE MODE: join this network and ping it, instead of "
                        "listening passively. The only way to control the sample rate.")
    vt.add_argument("--password", help="password for --ssid (never logged or committed)")
    vt.add_argument("--ping-ms", type=int, default=25, help="echo interval in active mode")
    vt.set_defaults(func=cmd_vitals)

    cs = sub.add_parser("csi", help="passive channel-state sensing: motion, and rogue APs by position")
    cs.add_argument("--port", required=True); cs.add_argument("--chip", choices=list(ARCHES))
    cs.add_argument("--channel", type=int, required=True, help="channel to pin 1-14")
    cs.add_argument("--bssid", help="restrict to one transmitter (recommended)")
    cs.add_argument("--seconds", type=int, default=60)
    cs.add_argument("--split-threshold", type=float, default=5.0,
                    help="per-subcarrier distance above which a profile is a different position "
                         "(default 1.0, calibrated on hardware: same radio measured 0.19, "
                         "nearest different radio 1.83)")
    cs.add_argument("--save", help="also write the raw CSI stream here")
    cs.set_defaults(func=cmd_csi)

    ft = sub.add_parser("ftm", help="802.11mc fine timing measurement — true distance, not RSSI")
    ft.add_argument("--port", required=True); ft.add_argument("--chip", choices=list(ARCHES))
    ft.add_argument("--channel", type=int, required=True)
    ft.add_argument("--bssid", help="peer to range (required unless --responder)")
    ft.add_argument("--responder", action="store_true",
                    help="be an FTM responder instead, so a second board can range this one")
    ft.add_argument("--survey", action="store_true",
                    help="try a ranging exchange with every AP in range; answers "
                         "'does anything here support 802.11mc at all' in one flash")
    ft.add_argument("--seconds", type=int, default=30)
    ft.add_argument("--frames", type=int, default=32,
                    help="FTM frames per burst (16/24/32/64); a peer may accept one and reject another")
    ft.add_argument("--burst", type=int, default=2, help="burst period in units of 100ms")
    ft.set_defaults(func=cmd_ftm)

    bs = sub.add_parser("ble-scan", help="scan/enumerate nearby BLE devices")
    bs.add_argument("--port", required=True); bs.add_argument("--chip", choices=list(ARCHES))
    bs.add_argument("--seconds", type=int, default=10)
    bs.set_defaults(func=cmd_ble_scan)

    cw = sub.add_parser("capture-wpa", help="authorized active reassociation assessment (disabled unless MEDUSA_ACTIVE_LAB=1)")
    cw.add_argument("--port", required=True); cw.add_argument("--chip", choices=list(ARCHES))
    cw.add_argument("--bssid", required=True); cw.add_argument("--channel", type=int, required=True)
    cw.add_argument("--client", help="authorized lab client STA MAC; omission requires an approved broadcast resilience test")
    cw.add_argument("--ssid", help="target ESSID (for the hc22000 line)")
    cw.add_argument("--seconds", type=int, default=25, help="capture window seconds")
    cw.set_defaults(func=cmd_capture_wpa)

    ud = sub.add_parser("unattended-dump", help="flush + retrieve the passive LittleFS inventory over USB")
    ud.add_argument("--port", required=True)
    ud.add_argument("--out", required=True, help="local destination CSV path")
    ud.add_argument("--timeout", type=float, default=15.0)
    ud.set_defaults(func=cmd_unattended_dump)

    a = p.parse_args()
    return a.func(a)

if __name__ == "__main__":
    sys.exit(main())
