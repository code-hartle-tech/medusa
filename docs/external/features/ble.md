# Bluetooth discovery

The current ESP firmware performs an **active BLE discovery scan**. It listens
for advertisements and may send standard scan requests, but it does not connect
to observed devices. The local web app says this before the run and requires the
operator to confirm that the scan will replace the board's current firmware.

The current strongest evidence is compile proof. Live device enumeration and
receiver-side behavior remain pending hardware gates.

## Current result

The board reports the fields it has actually parsed:

- device address;
- signal strength;
- advertised name when present;
- a vendor label derived from a small curated manufacturer-ID map.

The current product does not claim service enumeration, cross-session
first/last-seen history, pcap export, tracker classification, or a native phone
viewer. Those are separate implementation and privacy-review tasks.

Bluetooth addresses, names, and manufacturer data can still identify people or
devices. Keep the survey within the authorized area, use screen masking when
showing results, and remember that masking does not alter stored or downloaded
data.
