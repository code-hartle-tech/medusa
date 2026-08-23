# Medusa v1 — Brand Brief

Canonical reusable template style lives at
`~/Projects/dumpsock/assets/brand/v2_brief.md`. This is the Medusa
equivalent.

## Thesis

Medusa, the gorgon. One of three sisters; the mortal one. Originally a
priestess of Athena, transformed into a monster — beautiful weapon born
from violation, killed by Perseus using a mirror. Her severed head
retained the petrifying gaze; Perseus used it as a tool against worse
adversaries. In our retelling, **Medusa is the protector** — the wronged
become defenders.

For a wireless-recon tool:

- **Snake-hair** → multiple antennas (WiFi, BLE, ESP-NOW, optionally
  Sub-GHz). Many independent receivers, each "listening" in their own
  direction.
- **Petrifying gaze** → passive RF reconnaissance. Freezing the moment
  to inspect packets, beacons, devices. Read-only by default; we never
  "transmit aggression".
- **Perseus's mirror** → the operator never looks at the network
  directly. The phone (companion app) is the mirror; the MCU is the
  one staring into the medium.
- **Stone** → the **petrified** state of an audited device. Knowing
  exactly what it broadcasts. Captured frame-for-frame.

## Visual style

- Mostly white / stone-light interface
- Snake-green primary for the brand mark + CTAs
- Gaze-amber accent (the petrifying-eye color) for highlights + warnings
- Stone-gray for surfaces and dividers
- Mortal-ink for text
- Friendly rounded corners, generous whitespace
- Mascot prominent but not crowded
- **No** cyberpunk dark-mode drift (matches DumpSock v2 brief: light_only)

## Mascot

A stylized gorgon head — front-facing, single large eye (the petrifying
one) fixated on a small handheld device or radio spectrum visualization.
Snake hair fanning out, each snake's head distinct (visual cue: they
are individual antennas, not a homogeneous mane). Slightly ancient-Greek
sculptural feel, but cute — closer to "Pixar mythology" than "horror".

### Required mascot traits

- Front-facing head, one prominent eye
- 5-7 distinct snakes radiating from the head
- Stone-gray skin with subtle green tint
- Amber iris (the petrifying tint)
- Holding or facing a small screen/device
- Self-contained silhouette
- Cute, manga-adjacent, emoji-adjacent

### Avoid

- Realistic horror rendering
- Sexualized depiction (Medusa is often over-sexualized in mythology
  art — we want the friendly-tool angle, not the punished-victim angle)
- Blood, severed-head imagery
- Skulls, jolly-roger pirate stuff
- Cyberpunk neon / Tron grid backgrounds
- Antennas drawn as literal modern antenna shapes — keep them as
  stylized snakes

## Tone of voice

Same family as DumpSock: casual, witty, useful-first. Slightly more
ancient-Greek-flavored than DumpSock's "playful". A little
mythologically-self-aware. Never breathless about capabilities; always
defensive in framing.

Reference taglines (not yet picked):

- "The gorgon listens."
- "Don't look directly at the network. Look in the mirror."
- "Petrify the packets."
- "A HARTLE.TECH research tool — for the networks you own."

## Brand tokens

See `assets/brand/brand_tokens.yaml` for canonical palette + type.

## Non-negotiables

- Keep the legal posture in every visual: this is a **research tool**,
  not a weapon
- Keep the mythology playful, not horror
- Light theme only (no dark drift)
- Same fonts as DumpSock (Space Grotesk display + Inter body + JetBrains
  Mono code) so the HARTLE.TECH portfolio feels cohesive
