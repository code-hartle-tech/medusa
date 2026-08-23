#!/usr/bin/env python3
"""
esp_report — inventory + export + reporting for esp-hw scan/recon data.

Turns Medusa's live scans into the deliverables commercial tools paywall:
  - WiGLE CSV            (wardriving ecosystem interchange)
  - airodump-ng CSV      (aircrack / Wireshark pipeline interchange)
  - security-posture HTML (ranked risk report from RSN/PMF/WPS/cipher)
  - SQLite asset inventory (dedupe by BSSID, first/last-seen, running count)
  - OUI / vendor fingerprint (curated prefix map)

Pure stdlib, no hardware — fully unit-testable. CLI:
  esp_report.py export --format wigle|airodump|posture --in aps.json --out FILE [--db inv.sqlite]
  esp_report.py inventory --db inv.sqlite [--json]
"""
import argparse, csv, hashlib, hmac, html, io, json, os, secrets, sqlite3, sys, time

# Curated OUI prefix → vendor (first 3 octets, upper, colon-less). Not exhaustive —
# the common devices a home/SME audit meets. Unknown → "".
OUI = {
    "FCFBFB": "Cisco", "001A11": "Google", "3C5AB4": "Google", "F4F5E8": "Google",
    "DCA632": "Raspberry Pi", "B827EB": "Raspberry Pi", "E45F01": "Raspberry Pi", "2CCF67": "Raspberry Pi",
    "D83ADD": "Raspberry Pi", "28CDC1": "Raspberry Pi",
    "A4CF12": "Espressif", "246F28": "Espressif", "3C6105": "Espressif", "7CDFA1": "Espressif",
    "8CAAB5": "Espressif", "84F3EB": "Espressif", "24D7EB": "Espressif", "C4DEE2": "Espressif",
    "F412FA": "Espressif", "34AB95": "Espressif", "D8BFC0": "Espressif", "E8DB84": "Espressif",
    "F0F5BD": "TP-Link", "50C7BF": "TP-Link", "1C61B4": "TP-Link", "6470020": "TP-Link", "AC84C6": "TP-Link",
    "007F2E": "TP-Link", "B0BE76": "TP-Link", "9C5322": "TP-Link", "C006C3": "TP-Link",
    "DC0EA1": "TP-Link", "68FF7B": "TP-Link",
    "F0272D": "Apple", "F0DBF8": "Apple", "A85C2C": "Apple", "3C0754": "Apple", "047BCB": "Apple",
    "8C8590": "Apple", "D0817A": "Apple", "F02475": "Apple", "A4B197": "Apple", "BC926B": "Apple",
    "88665A": "Apple", "DC2B2A": "Apple", "6C4008": "Apple",
    "F0EE10": "Samsung", "5CE8EB": "Samsung", "8425DB": "Samsung", "E8508B": "Samsung", "BC8385": "Samsung",
    "78BDBC": "Samsung", "D0176A": "Samsung",
    "001A79": "Intel", "3C9863": "Intel", "8C554A": "Intel", "A0A8CD": "Intel", "347DF6": "Intel",
    "9C305B": "Xiaomi", "64B473": "Xiaomi", "F8A45F": "Xiaomi", "286C07": "Xiaomi",
    "D46E0E": "Reolink", "EC71DB": "Reolink",
    "50EC50": "Amazon", "68F728": "Amazon", "FCA667": "Amazon", "44650D": "Amazon",
    "18B430": "Nest", "641666": "Netgear", "A040A0": "Netgear", "204E7F": "Netgear",
}


def oui_vendor(mac):
    """Vendor for a MAC by OUI prefix; '' if unknown. Handles colon/colonless."""
    if not mac:
        return ""
    h = mac.replace(":", "").replace("-", "").upper()
    if len(h) < 6:
        return ""
    return OUI.get(h[:6], "")


def _ts(now=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now if now is not None else time.time()))


# --------------------------------------------------------------------------- #
# exports
# --------------------------------------------------------------------------- #
def _authmode(row):
    """A WiGLE/airodump-style bracketed auth string from our fields."""
    auth = (row.get("auth") or "").strip().upper()
    cipher = (row.get("cipher") or "").upper()
    if not auth or auth == "UNKNOWN":
        return "[UNKNOWN][ESS]"
    if auth == "OPEN":
        return "[ESS]"
    parts = auth.replace("/", "-")
    inner = parts + ("-" + cipher if cipher and cipher not in ("", "NONE") else "")
    return f"[{inner}][ESS]"


class Pseudonymizer:
    """Replace identifiers in an export that is going to leave the operator.

    The product UI can mask SSIDs, Bluetooth names, and hardware addresses on
    screen, but every export format wrote them in full — including the posture
    report, which is the artifact most likely to be handed to somebody else. A
    survey of an owned network still records third-party devices, and the
    threat model treats those identifiers as personal data.

    This is pseudonymisation, not anonymisation, and the distinction matters:

    - The vendor OUI is preserved, because vendor mix is the point of an
      inventory and the first three octets identify a manufacturer, not a
      device. The device-specific octets are replaced.
    - Output stays a syntactically valid MAC so downstream tools still parse
      it, and distinct inputs stay distinct so counts and per-AP rows survive.
    - The salt is random per instance and never written out, so two reports of
      the same estate cannot be correlated with each other.
    - Anyone holding the original capture can still re-derive the mapping.
      This protects the shared artifact, not the capture it came from.
    """

    def __init__(self, salt=None):
        self._salt = secrets.token_bytes(16) if salt is None else salt

    def _digest(self, kind, value, length):
        mac = hmac.new(self._salt, f"{kind}:{value}".encode("utf-8", "replace"), hashlib.sha256)
        return mac.digest()[:length]

    def bssid(self, value):
        if not value:
            return ""
        parts = str(value).strip().upper().replace("-", ":").split(":")
        if len(parts) != 6 or not all(len(p) == 2 for p in parts):
            # Not a MAC we recognise — replace the whole thing rather than
            # leaking an unexpected format through unchanged.
            return "".join(f"{b:02X}" for b in self._digest("opaque", value, 6))
        tail = self._digest("bssid", ":".join(parts), 3)
        return ":".join(parts[:3] + [f"{b:02X}" for b in tail])

    def ssid(self, value):
        # A hidden network has no name; inventing one would be a false record.
        if not value:
            return ""
        token = self._digest("ssid", str(value), 3).hex()
        return f"network-{token}"


def _pseudonymize_rows(rows, pseudonymizer):
    """Return rows with identifiers replaced, leaving every other field alone."""
    if pseudonymizer is None:
        return rows
    masked = []
    for row in rows:
        copy = dict(row)
        if "bssid" in copy:
            copy["bssid"] = pseudonymizer.bssid(copy.get("bssid"))
        if "ssid" in copy:
            copy["ssid"] = pseudonymizer.ssid(copy.get("ssid"))
        masked.append(copy)
    return masked


def _spreadsheet_safe(value):
    """Neutralize radio-controlled text that spreadsheet apps may execute."""
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _write_spaced_csv_row(out, values):
    """Write valid CSV while retaining airodump-ng's comma-space layout."""
    row = io.StringIO(newline="")
    csv.writer(row, lineterminator="\n").writerow(values)
    encoded = row.getvalue()
    rendered = []
    quoted = False
    for char in encoded:
        if char == '"':
            quoted = not quoted
        rendered.append(char)
        if char == "," and not quoted:
            rendered.append(" ")
    out.write("".join(rendered))


def wigle_csv(rows, now=None, pseudonymizer=None):
    """WiGLE 'WigleWifi-1.4' CSV. Lat/lon 0 unless a row carries lat/lon (no GPS → 0)."""
    rows = _pseudonymize_rows(rows, pseudonymizer)
    out = io.StringIO()
    out.write("WigleWifi-1.4,appRelease=medusa,model=esp,release=1,device=medusa,display=,board=esp,brand=hartle.tech\n")
    w = csv.writer(out)
    w.writerow(["MAC", "SSID", "AuthMode", "FirstSeen", "Channel", "RSSI",
                "CurrentLatitude", "CurrentLongitude", "AltitudeMeters", "AccuracyMeters", "Type"])
    ts = _ts(now)
    for r in rows:
        w.writerow([r.get("bssid", ""), _spreadsheet_safe(r.get("ssid", "")), _authmode(r),
                    r.get("first_seen", ts), r.get("channel", ""), r.get("rssi", ""),
                    r.get("lat", 0.0), r.get("lon", 0.0), 0.0, 0.0, "WIFI"])
    return out.getvalue()


def airodump_csv(rows, now=None, pseudonymizer=None):
    """airodump-ng-style CSV (AP section) — drops into the aircrack toolchain."""
    rows = _pseudonymize_rows(rows, pseudonymizer)
    out = io.StringIO()
    ts = _ts(now)
    _write_spaced_csv_row(out, [
        "BSSID", "First time seen", "Last time seen", "channel", "Speed", "Privacy",
        "Cipher", "Authentication", "Power", "# beacons", "# IV", "LAN IP",
        "ID-length", "ESSID", "Key",
    ])
    for r in rows:
        auth = (r.get("auth") or "").strip().upper()
        priv = "UNKNOWN" if not auth or auth == "UNKNOWN" else ("OPN" if auth == "OPEN" else "WEP" if auth == "WEP" else "WPA2" if "WPA2" in auth else "WPA3" if "WPA3" in auth else "WPA")
        ssid = "" if r.get("ssid") is None else str(r.get("ssid", ""))
        _write_spaced_csv_row(out, [
            r.get("bssid", ""), r.get("first_seen", ts), r.get("last_seen", ts), r.get("channel", ""),
            54, priv, (r.get("cipher") or "").upper(), "PSK" if priv.startswith("WPA") else "",
            r.get("rssi", ""), r.get("count", 0), 0, "0.0.0.0", len(ssid), _spreadsheet_safe(ssid), "",
        ])
    return out.getvalue()


def _risk(row):
    """(severity 0-3, label, why) for an AP's posture. 3=critical."""
    auth = (row.get("auth") or "").strip().upper()
    if auth in ("", "UNKNOWN"):
        return 0, "unknown", "Security capabilities were not collected; run a security profile before assessing this network."
    if auth == "OPEN":
        return 3, "critical", "The AP advertises an open configuration with no link-layer encryption; verify guest isolation and application-layer protections, or enable WPA2/WPA3."
    if auth == "WEP":
        return 3, "critical", "The AP advertises deprecated WEP; migrate the network and its clients to WPA2/WPA3."
    if row.get("wps"):
        return 2, "high", "WPS is advertised; disable it when push-button or PIN enrollment is not required and verify the enrollment policy."
    pmf = (row.get("pmf") or "").strip().lower()
    if auth in ("WPA2-PSK", "WPA2", "WPA1") and pmf == "none":
        return 1, "medium", "Protected Management Frames were not advertised; enable them where supported and verify client compatibility."
    if auth in ("WPA2-PSK", "WPA2", "WPA1") and not pmf:
        return 0, "unknown", "PMF capability was not collected; run a security profile before assessing management-frame protection."
    if "WPA3" in auth:
        return 0, "ok", "WPA3/SAE is advertised; verify PMF requirements and transition-mode policy against the configured client estate."
    return 1, "medium", "A legacy or mixed authentication configuration is advertised; review PMF, cipher, and transition policy."


def posture_html(rows, now=None, pseudonymizer=None):
    """Self-contained security-posture report from recon rows (ranked by risk)."""
    rows = _pseudonymize_rows(rows, pseudonymizer)
    ranked = sorted(({**r, "_r": _risk(r)} for r in rows), key=lambda r: (-r["_r"][0], r.get("ssid", "")))
    color = {"critical": "#ff5d47", "high": "#ffb03a", "medium": "#f0c040", "ok": "#56e39f", "unknown": "#93a2ba"}
    n_crit = sum(1 for r in ranked if r["_r"][1] == "critical")
    n_high = sum(1 for r in ranked if r["_r"][1] == "high")
    body = []
    for r in ranked:
        sev, label, why = r["_r"]
        auth_text = (r.get("auth") or "UNKNOWN").upper()
        pmf_text = r.get("pmf") or "unknown"
        wps_text = "unknown" if "wps" not in r or r.get("wps") is None else ("on" if r.get("wps") else "off")
        cipher_text = (r.get("cipher") or "UNKNOWN").upper()
        body.append(
            f'<tr><td><b>{html.escape(r.get("ssid","") or "(hidden)")}</b><br>'
            f'<code>{html.escape(r.get("bssid",""))}</code> {html.escape(oui_vendor(r.get("bssid","")))}</td>'
            f'<td>{html.escape(auth_text)}</td>'
            f'<td>{html.escape(pmf_text)}</td>'
            f'<td>{wps_text}</td>'
            f'<td>{html.escape(cipher_text)}</td>'
            f'<td style="color:{color.get(label,"#ccc")};font-weight:600">{label}</td>'
            f'<td>{html.escape(why)}</td></tr>')
    return (
        "<!doctype html><meta charset=utf-8><title>Medusa — Wi-Fi security posture</title>"
        "<style>body{font:14px/1.5 system-ui,sans-serif;background:#0a0e16;color:#cbd6e6;margin:0;padding:32px}"
        "h1{font-family:ui-monospace,monospace;color:#a66cff;letter-spacing:.04em}"
        ".sum{margin:12px 0 22px;color:#93a2ba}.sum b{color:#eaf1fb}"
        "table{border-collapse:collapse;width:100%;font-size:13px}"
        "th,td{text-align:left;padding:9px 12px;border-bottom:1px solid rgba(126,160,214,.14);vertical-align:top}"
        "th{font-family:ui-monospace,monospace;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#6b7a92}"
        "code{color:#34e0d0}</style>"
        f"<h1>Wi-Fi security posture</h1><div class=sum>Generated {_ts(now)} · HARTLE.TECH Medusa · "
        f"<b>{len(ranked)}</b> networks · <b style='color:#ff5d47'>{n_crit}</b> critical · "
        f"<b style='color:#ffb03a'>{n_high}</b> high</div>"
        + ("<div class=sum style='border-left:3px solid #a66cff;padding-left:10px'>"
           "Network names and hardware addresses in this report are "
           "<b>pseudonyms</b>, not observed values. Vendor prefixes are real; the "
           "device-specific octets are randomised per report, so identifiers here "
           "cannot be matched against another report or looked up. The security "
           "findings are unaffected.</div>" if pseudonymizer is not None else "")
        + "<table><tr><th>Network</th><th>Auth</th><th>PMF</th><th>WPS</th><th>Cipher</th><th>Risk</th><th>Finding</th></tr>"
        + "".join(body) + "</table>")


# --------------------------------------------------------------------------- #
# inventory (SQLite)
# --------------------------------------------------------------------------- #
def init_db(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS aps (
        bssid TEXT PRIMARY KEY, ssid TEXT, channel INTEGER, rssi INTEGER,
        auth TEXT, pmf TEXT, wps INTEGER, cipher TEXT, vendor TEXT,
        first_seen TEXT, last_seen TEXT, count INTEGER DEFAULT 1)""")
    db.commit()
    return db


def upsert_aps(path, rows, now=None):
    """Insert/refresh APs by BSSID; bumps last_seen + count, keeps first_seen."""
    db = init_db(path)
    ts = _ts(now)
    for r in rows:
        b = (r.get("bssid") or "").upper()
        if not b:
            continue
        v = oui_vendor(b)
        cur = db.execute("SELECT count FROM aps WHERE bssid=?", (b,)).fetchone()
        if cur:
            db.execute("""UPDATE aps SET ssid=?, channel=?, rssi=?, auth=COALESCE(?,auth),
                          pmf=COALESCE(?,pmf), wps=COALESCE(?,wps), cipher=COALESCE(?,cipher),
                          vendor=?, last_seen=?, count=count+1 WHERE bssid=?""",
                       (r.get("ssid", ""), r.get("channel"), r.get("rssi"), r.get("auth"),
                        r.get("pmf"), (1 if r.get("wps") else None) if "wps" in r else None,
                        r.get("cipher"), v, ts, b))
        else:
            db.execute("""INSERT INTO aps (bssid,ssid,channel,rssi,auth,pmf,wps,cipher,vendor,first_seen,last_seen,count)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                       (b, r.get("ssid", ""), r.get("channel"), r.get("rssi"), r.get("auth"),
                        r.get("pmf"), 1 if r.get("wps") else 0, r.get("cipher"), v, ts, ts))
    db.commit()
    n = db.execute("SELECT COUNT(*) FROM aps").fetchone()[0]
    db.close()
    return n


def all_aps(path):
    if not os.path.exists(path):
        return []
    db = init_db(path)
    cols = ["bssid", "ssid", "channel", "rssi", "auth", "pmf", "wps", "cipher", "vendor", "first_seen", "last_seen", "count"]
    rows = [dict(zip(cols, r)) for r in db.execute(f"SELECT {','.join(cols)} FROM aps ORDER BY last_seen DESC")]
    db.close()
    for r in rows:
        r["wps"] = bool(r["wps"])
    return rows


# --------------------------------------------------------------------------- #
def _load_rows(path):
    with open(path) as f:
        d = json.load(f)
    return d.get("aps", d) if isinstance(d, dict) else d


def cmd_export(a):
    rows = _load_rows(a.infile)
    # The local inventory keeps real identifiers; only the exported artifact is
    # pseudonymised. Redacting the operator's own database would destroy the
    # dedupe key and their ability to re-identify their own estate.
    if a.db:
        upsert_aps(a.db, rows)
    gen = {"wigle": wigle_csv, "airodump": airodump_csv, "posture": posture_html}[a.format]
    pseudonymizer = Pseudonymizer() if a.pseudonymize else None
    text = gen(rows, pseudonymizer=pseudonymizer)
    with open(a.out, "w") as f:
        f.write(text)
    print("EXPORT_JSON " + json.dumps({
        "path": a.out, "format": a.format, "count": len(rows),
        "pseudonymized": bool(a.pseudonymize),
    }))
    return 0


def cmd_inventory(a):
    rows = all_aps(a.db)
    if a.json:
        print("INVENTORY_JSON " + json.dumps({"aps": rows}))
    else:
        for r in rows:
            print(f"{r['bssid']}  {r['ssid'][:24]:24}  ch{r['channel']}  {r['auth'] or '?'}  x{r['count']}  {r['vendor']}")
    return 0


def main():
    p = argparse.ArgumentParser(prog="esp_report")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--format", required=True, choices=["wigle", "airodump", "posture"])
    e.add_argument("--in", dest="infile", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--db")
    e.add_argument("--pseudonymize", action="store_true",
                   help="Replace SSIDs and the device octets of each BSSID in the exported "
                        "artifact. Vendor prefixes and every security finding are preserved. "
                        "Use when the export leaves your hands; the local inventory is unaffected.")
    e.set_defaults(func=cmd_export)
    iv = sub.add_parser("inventory")
    iv.add_argument("--db", required=True)
    iv.add_argument("--json", action="store_true")
    iv.set_defaults(func=cmd_inventory)
    a = p.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
