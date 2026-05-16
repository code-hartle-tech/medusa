# Medusa — Legal Frame (seed; expand in M1)

Status: **seed**. Researcher-pass during M1 expands per-jurisdiction
specifics. Operator + counsel review before public surfacing.

## Posture in one paragraph

Medusa is published for security researchers, network operators
auditing their own infrastructure, educators, and CTF participants.
Capabilities documented are **passive** (sniff, scan, parse). Use on
networks the operator does not own, administer, or have authorisation
to test is **illegal in most jurisdictions** and not endorsed by
HARTLE.TECH. Every public artifact carries the lawful-use clause
(`NOTICE`, Section 4(c)).

## Jurisdictional pointers (to be expanded)

### European Union (GDPR + ePrivacy)

- Passive RF capture records personal data (MAC addresses, device
  identifiers, BLE service UUIDs, probe-request SSIDs).
- The operator using Medusa to audit their own infrastructure is a
  **controller** (Art. 4 GDPR); captures are stored on the operator's
  device; no cloud transmission by default.
- Companion app must surface a first-launch notice that captures
  contain personal data; retention is operator-configurable.
- Article 5(1)(c) data-minimisation: capture filters should be on by
  default to limit what's stored to what's necessary for the audit.
- ePrivacy: passive reception of unencrypted broadcasts (beacons,
  probe requests) is technically the receipt of communications
  intentionally broadcast for anyone to receive; courts have generally
  not treated this as interception. Active capture of unicast traffic
  is a different question — Medusa does not do that by default.

### United States

- **18 U.S.C. § 2511** (Wiretap Act): prohibits intercepting "wire,
  oral, or electronic" communications without consent. Pure beacon /
  probe-request capture is generally treated as the consensual receipt
  of broadcasts; **unicast 802.11 frame capture is in gray-area
  territory** depending on circuit.
- Authorisation from the network owner is the cleanest path. Medusa
  documentation requires it.
- **18 U.S.C. § 1030** (CFAA): Medusa does not access protected
  computers, so the CFAA isn't directly implicated by passive capture
  — but is the moment any capability "actively interacts" with a
  target, which Medusa explicitly doesn't.

### Portugal (HARTLE.TECH's home jurisdiction)

- Constitution Art. 34: communication secrecy.
- Law 41/2004 (ePrivacy transposition).
- GDPR via Law 58/2019.
- Cybercrime Law 109/2009: defines "illegitimate access" to information
  systems; passive RF reception generally is not access.
- Authorisation requirement, data-minimisation, local storage:
  alignment-by-design.

### General principles (every jurisdiction)

- **Own network**: clearly legal.
- **Network where you have written authorisation**: clearly legal.
- **Public hotspot you're a legitimate user of**: ambiguous; passive
  reception of broadcasts is usually fine; capturing other users'
  unicast traffic is usually not.
- **Network you don't own and aren't authorised for**: not OK in any
  jurisdiction the project targets.

## What the documentation must NOT say

- Anything that frames Medusa as a "tool for getting into other
  people's networks"
- Step-by-step "how to attack X" descriptions
- Pre-packaged scripts that target unconsented networks
- Boasts about evading detection on a network you're not on

## What the documentation SHOULD say (template phrases)

- "Audit the radio surface of your own network"
- "Understand what your devices are broadcasting"
- "Educational tool for security researchers"
- "For use on networks you own or are authorised to test"
- "Lawful use only — see `NOTICE` Section 4(c)"

## Reviewer pass (mandatory before any public surfacing)

See `design/threat-model.md` for the 5-question reviewer checklist.

## Open questions for counsel (deferred, not blocking bootstrap)

- [ ] Brand-name conflicts — "Medusa" is a common name in cyber tooling
      (Medusa SQL pentest, Medusa security framework). Check trademark
      register; we're using it as a project codename + brand, not a
      registered mark, but worth a search.
- [ ] Export-control review: ESP32-based monitoring tools are not
      generally classified as munitions, but worth a confirmatory pass
      if shipped to certain jurisdictions.
- [ ] CE marking / FCC Part 15: if Medusa is sold as hardware (not just
      open-source design), it needs RF compliance testing. Currently
      we ship designs + firmware, not assembled hardware.
