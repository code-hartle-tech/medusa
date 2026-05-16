# Medusa — Claude house rules

Inherits all org-wide HARTLE.TECH rules (Nine Golden Rules, Claude
operating context, GitHub conventions, etc.). For the full org-wide
handoff, see `hartle-tech-wiki/docs/internal/claude/handoff.md` (served
at https://void.hartle.tech/claude/handoff over Tailscale).

This file documents what's project-specific.

## Project shape

- **Repo**: `code-hartle-tech/medusa` (PUBLIC, Apache 2.0)
- **Project board**: 🐍 Medusa Quest Board (GitHub project)
- **MCU**: ESP32-S3 (WROOM-1 module). Other options (Pi Pico 2 W,
  ESP8266 NodeMCU, XIAO-RP2040) are documented as fallbacks but
  **don't drift** unless a hard blocker forces it.
- **Sibling refs**: read `~/Projects/nosferatos/` (NSFAS lane voice),
  `~/Projects/neartrace-android-mvp/` (Android companion pattern),
  `~/Projects/dumpsock/` (newest HARTLE.TECH brand template), and
  `~/Projects/hartle.tech-terraform/` (IaC patterns) before making
  architectural choices.

## Build / dev (placeholder until firmware scaffold lands)

When `firmware/` is real:

```bash
cd firmware/
pio run                    # build for ESP32-S3
pio run --target upload    # flash via USB-C
pio device monitor         # serial console (baud per platformio.ini)
```

Until then: `firmware/` is empty.

## Brand

Mythological — gorgon Medusa. Antennas as snake-hair, RF capture as
"petrifying gaze". Palette + type tokens are in
`assets/brand/brand_tokens.yaml` (will be filled in M3 of bootstrap).
Mascot SVG eventually at `assets/brand/medusa-mascot.svg`. **Don't
eyeball the brand**; brand_tokens.yaml is canonical.

## Legal posture (read every session, internalize)

This project is **research and educational**. Public-facing artifacts
(README, docs/, public commit prose, GitHub issue descriptions) must:

- Frame capabilities **defensively** ("audit your own network", not
  "attack any network")
- Avoid documenting offensive utility against third parties
- Preserve plausible deniability (no operator-intent statements)
- Carry the lawful-use clause from `NOTICE` in any derivative work

Internal docs (in `hartle-tech-wiki/docs/internal/medusa/`, served
tailnet-only at `void.hartle.tech/medusa/`) can be candid about *how*
each capability works — those notes are not public.

When Claude (or any swarm-spawned agent) produces public-facing copy:
**reviewer pass mandatory**. If it sounds like a how-to-attack-grandma's-wifi
tutorial, reject + rewrite.

## Things that need explicit operator approval

- Buying hardware (Rule #2)
- Creating any external account, service, or paid SaaS
- Joining the primary tailnet `hartley-pancake` (Rule #4)
- Making the repo private (we landed on public per the OSS philosophy
  established with DumpSock)
- Anything that touches `hartle.tech-terraform` directly (DNS records,
  Tailscale ACL changes) — operator reviews via PR

## Memory + handoff

When a session does meaningful work, before ending: update
`hartle-tech-wiki/docs/internal/medusa/sessions/<YYYY-MM-DD>.md` with
the episode summary (TBBT-style title encouraged), plus update
`~/.claude/projects/-Users-vz-Projects-medusa/memory/` with any rules
the operator gave + project state shifts.
