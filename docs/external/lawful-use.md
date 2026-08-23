# Authorized use and data care

Medusa is research and educational software for security researchers, network
operators, educators, and CTF participants. HARTLE.TECH supports its use only
on hardware, networks, and in physical survey areas the operator owns,
administers, or has explicit written permission to assess.

Authorization is necessary, but it is not by itself a legal conclusion. Radio
collection can also engage privacy, interception, employment, property,
spectrum, and data-protection rules. Those obligations depend on the data,
method, location, people affected, and terms of the engagement.

## Before a survey

- Keep a written scope identifying the environment, devices, methods, time
  window, data recipients, and stop contact.
- Collect the minimum identifiers needed for the defensive purpose.
- Define retention and deletion before collection starts.
- Confirm whether consent, notice, a data-protection assessment, or local
  counsel review is required.
- Do not treat an SSID, public hotspot, or radio broadcast as blanket consent.
- Do not enable a transmitting method merely because a software control allows
  it. Medusa's required firmware active-transmission safeguards are not
  implemented yet.

## Current product boundary

- The web app shows a first-use personal-data notice and masks identifiers on
  screen by default.
- Masking is presentation-only; device records and downloads remain unredacted.
- Device and host data stay local by default; no cloud upload path is enabled.
- Retention is manual. Closing the browser does not erase the board, temporary
  host artifacts, or files already downloaded.
- Wi-Fi and Bluetooth discovery scans may transmit ordinary probe or scan
  requests. They are not described as passive.

## Legal references for counsel

Potentially relevant instruments include the EU GDPR and ePrivacy framework,
United States 18 U.S.C. §§ 2511 and 1030, and in Portugal the Constitution's
communications protections, Law 41/2004, Law 58/2019, and Cybercrime Law
109/2009. This list is a research pointer, not an interpretation or a verdict
about a particular survey.

Consult qualified counsel for the actual jurisdiction and engagement. This page
does not state that ownership, authorization, passive reception, or use of a
public network is automatically lawful.

## Project notice and license

[`NOTICE`](https://github.com/code-hartle-tech/medusa/blob/develop/NOTICE)
records HARTLE.TECH's intended use and support scope. Medusa remains licensed
under Apache-2.0; `LICENSE` contains the redistribution terms. The lawful-use
statement does not add a restriction to that license.

Every public change receives the defensive-framing and proof-truth review in the
[threat model](https://github.com/code-hartle-tech/medusa/blob/develop/wiki/design/threat-model.md).
