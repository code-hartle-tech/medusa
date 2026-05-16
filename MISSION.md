# Medusa — Mission

**Project**: medusa
**Owner**: HARTLE.TECH (NIPC 518241327, Porto, Portugal)
**Public identity**: HARTLE.TECH + `contact@hartle.tech` only
**Git author**: `hartle-tech <noreply…>`
**License**: Apache 2.0

## What

A microcontroller-based wireless-recon tool that lives in a phone case,
paired with a companion mobile app for the UI. Sibling project to
**Nosferato** (NSFAS single-board-computer lane, Pi Zero 2 W) and
companion to **NearTrace** (Android BLE scanner). Together they form the
"phone-as-UI hacking case kit" arc.

## MCU pick (pinned)

**ESP32-S3** (WROOM-1 module variant for case integration). Validate
against the alternatives only if a hard blocker appears. Reasoning:

- WiFi 2.4 GHz monitor mode + BLE 5 + USB-OTG (BadUSB-capable)
- Mature pentest ecosystem (Marauder, Bruce, Pwnagotchi-S3 forks)
- Dual-core, AI accelerators, large RAM for packet capture buffers

Backups: Pi Pico 2 W (RP2350 + CYW43439) for any feature ESP32-S3 can't
deliver. ESP8266 and XIAO-RP2040 are display/companion-MCU roles only.

## Tone

- **Internal docs**: cyberpunk + TBBT-narrative, episode numbers,
  memes / GIFs welcome, written for future-Claude + future-operator
  picking the project up cold.
- **Public docs**: legal-eagle posture. Defensive / educational framing
  for any capability. No malicious-utility documentation. Plausible
  deniability around offense, explicit about lawful-use scope.

## Non-negotiables (Nine Golden Rules abbreviated)

1. **No secrets in any repo, ever** — Cortex paths only.
2. **No spend without explicit operator yes** — no paid services, no
   parts-buying decisions, no LLM API tier-ups.
3. **HARTLE.TECH identity only** on every public artifact.
4. **Per-project distinctness** — new repo, new board, new labels;
   inherit shared platforms (Cortex, GitHub org, CF DNS, Tailscale).
5. **Everything is IaC** — CF DNS via `hartle.tech-terraform`;
   any system service via Ansible.
6. **Trust but verify** — every research/output gets a reviewer pass.
7. **Workflow setup precedes code** — issue → branch → narrative commit.
8. **Test before push** — local build + smoke test.
9. **Ship completes only after docs + memory + handoff** updated.

## Deliverables (what "bootstrapped" looks like)

- [x] Repo skeleton (this commit)
- [x] LICENSE (Apache 2.0) + NOTICE (HARTLE.TECH credit + lawful-use)
- [x] .github/FUNDING.yml placeholders
- [x] MISSION.md (this file)
- [x] CLAUDE.md (project house rules)
- [x] README.md (public-facing)
- [x] Directory scaffolds: hardware/, design/, research/, assets/brand/,
      firmware/, docs/external/
- [ ] GitHub repo created (public, Apache 2.0)
- [ ] Project board "🐍 Medusa Quest Board"
- [ ] Seed issues (8–12)
- [ ] CF DNS: medusa.hartle.tech → GitHub Pages (CNAME pattern; subdomain
      = CNAME to `code-hartle-tech.github.io`, NEVER 4 A records — see
      memory `feedback_pages_subdomain_cname.md`)
- [ ] Wiki section: `hartle-tech-wiki/docs/internal/medusa/`

## Sibling references (read first)

- `~/Projects/nosferatos/` — NSFAS Pi rig, brand house style, threat
  model precedent
- `~/Projects/neartrace-android-mvp/` — Android companion-app pattern,
  Nine Golden Rules canonical CLAUDE.md
- `~/Projects/dumpsock/` — newest HARTLE.TECH brand template
  (`assets/brand/v2_brief.md` + `brand_tokens.yaml`), Pages + Caddy
  hybrid deploy, Apache-2.0 OSS-ification pattern
- `~/Projects/hartle.tech-terraform/` — CF DNS + Ansible role patterns
  (`dumpsock_docs` role is the per-product self-host template)

## Brand thesis (one-paragraph seed)

Medusa, the gorgon. Three sisters; Medusa the mortal one. Snake-hair
become antennas. The petrifying gaze becomes passive RF reconnaissance:
freezing the moment to inspect packets, devices, beacons. Killed by
Perseus using a mirror — *don't look directly at adversaries; look at
their reflection in the medium*. The mascot is a gorgon head with a
single eye fixated on a screen of radio spectrum, snakes-as-antennas
fanning out around it. Palette: serpent-green primary, gaze-amber
accent (the petrification effect), stone-gray for background, mortal-ink
for type.

Full brand brief in `assets/brand/v1_brief.md`.
