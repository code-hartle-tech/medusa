/*
 * medusa_badusb — USB HID keystroke injection (BadUSB / Rubber-Ducky class).
 *
 * On enumeration, types TARGET_PAYLOAD as a USB keyboard. Needs native USB-OTG:
 * ESP32-S3 (build with USBMode=default / TinyUSB) or the RP2040/RP2350 lane. The
 * ESP32-C3/C6 expose only USB-Serial-JTAG and cannot present an arbitrary HID
 * device, so this firmware is S3/RP-only by design.
 *
 * LAWFUL USE ONLY — your own machines / authorized engagements. Default payload is
 * a benign banner; set TARGET_PAYLOAD via medusa_target.h for a real test.
 */
#include <Arduino.h>

#if ARDUINO_USB_MODE == 1
#error "medusa_badusb needs USB-OTG / TinyUSB mode — build the S3 with USBMode=default."
#endif

#include "USB.h"
#include "USBHIDKeyboard.h"

#if defined(__has_include)
#  if __has_include("medusa_target.h")
#    include "medusa_target.h"
#  endif
#endif
#ifndef TARGET_PAYLOAD
#define TARGET_PAYLOAD "echo Medusa BadUSB demo - HARTLE.TECH"
#endif

USBHIDKeyboard Keyboard;

void setup() {
  Keyboard.begin();
  USB.begin();
  delay(2500);                 // let the host enumerate the HID device
  Keyboard.println(TARGET_PAYLOAD);
}

void loop() {
  delay(1000);                 // one-shot payload; idle afterwards
}
