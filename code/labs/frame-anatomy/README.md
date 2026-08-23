# Offline 802.11 frame-anatomy lab

This small Python lab turns two IEEE 802.11 management-frame shapes into
inspectable bytes:

- a category-127 vendor-specific action frame; and
- a deauthentication frame.

It is a byte-layout lesson, not a radio tool. `frame_anatomy.py` uses only the
Python standard library, opens no sockets or radios, performs no capture, and
contains no transmit path. It can write raw, FCS-free specimens and classic
PCAP files with `LINKTYPE_IEEE802_11` (105) for offline inspection.

## Try it

Run the tests first:

```sh
python3 -m unittest discover -s tests -v
```

Build a harmless local specimen using placeholder addresses:

```sh
python3 frame_anatomy.py build-action \
  --receiver 02:00:00:00:00:01 \
  --transmitter 02:00:00:00:00:02 \
  --bssid 02:00:00:00:00:03 \
  --oui aa:bb:cc \
  --vendor-content-hex 010203 \
  --raw-out action.bin \
  --pcap-out action.pcap
```

Here `aa:bb:cc` is an unassigned lab placeholder carried in the protocol's
three-octet OUI field; it is not claimed as an IEEE-assigned identifier.

Decode the file back to labelled JSON:

```sh
python3 frame_anatomy.py decode action.bin
python3 frame_anatomy.py decode action.pcap
```

`build-deauth` has the same address/output arguments and requires a numeric
`--reason-code`. It also only creates a regular file or prints JSON; it cannot
send the resulting bytes anywhere. Run `python3 frame_anatomy.py --help` for
the complete CLI.

### Worked 26-byte specimen

This command uses three distinct locally administered placeholder addresses,
a visible Sequence Control value, and reason code 2:

```sh
python3 frame_anatomy.py build-deauth \
  --receiver 02:00:00:00:00:01 \
  --transmitter 02:00:00:00:00:02 \
  --bssid 02:00:00:00:00:03 \
  --duration 0 \
  --sequence 291 \
  --fragment 4 \
  --reason-code 2
```

The reproducible FCS-free result is:

```text
c000000002000000000102000000000202000000000334120200
```

Read it by offset rather than as one opaque hexadecimal string:

| Offset | Bytes | Field |
| ---: | --- | --- |
| `0..1` | `c0 00` | Frame Control: management type, deauthentication subtype |
| `2..3` | `00 00` | Duration |
| `4..9` | `02 00 00 00 00 01` | Address 1: receiver/destination placeholder |
| `10..15` | `02 00 00 00 00 02` | Address 2: transmitter/source placeholder |
| `16..21` | `02 00 00 00 00 03` | Address 3: BSSID placeholder |
| `22..23` | `34 12` | Sequence Control `0x1234`: sequence 291, fragment 4 |
| `24..25` | `02 00` | Reason code 2, little-endian |

Marauder's pinned [26-byte template](https://github.com/justcallmekoko/ESP32Marauder/blob/7543febcd245f15783ce629698438510d2f35f01/esp32_marauder/WiFiScan.h#L582-L588)
has the same field boundaries. Its [sender](https://github.com/justcallmekoko/ESP32Marauder/blob/7543febcd245f15783ce629698438510d2f35f01/esp32_marauder/WiFiScan.cpp#L8897-L8957)
fills address fields before attempting raw TX, but does not check those TX
return values. This lab stops at byte construction so packet shape, API
acceptance, over-the-air capture, and receiver action remain separate claims.

## What to look at

The first two octets are Frame Control in little-endian order. With both DS
flags clear, management-frame addresses map as follows:

| Field | Meaning in this lab |
| --- | --- |
| Address 1 | receiver / destination |
| Address 2 | transmitter / source |
| Address 3 | BSSID |

Duration, Sequence Control, and the deauthentication reason field are also
little-endian. Sequence Control stores the four-bit fragment number below the
twelve-bit sequence number. Builders stop at the end of the MAC body: they do
**not** append the four-byte Frame Check Sequence (FCS). PCAP record lengths
therefore describe those same FCS-free bytes.

The library intentionally supports only these two shapes, protocol version 0,
management type, DS flags clear, full (not truncated) PCAP records, and a
lab-defined 4095-byte input bound. Unsupported input fails closed with
`FrameAnatomyError` or `PcapError`.

## Lawful use

Medusa is research and educational software for use on networks the operator
owns, administers, or has explicit written permission to test. Use against
networks or devices without authorization is illegal in most jurisdictions.
HARTLE.TECH publishes this work for security researchers, network operators
auditing their own infrastructure, educators, and capture-the-flag
participants — not for unauthorized intrusion.

Preserve the repository [`NOTICE`](../../../NOTICE) and the attribution
“Medusa — © HARTLE.TECH” in derivative distributions.
