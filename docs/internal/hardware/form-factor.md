# Medusa — Form Factor (seed; refine in M2)

Status: **seed**. v1 targets a single phone model; v2 expands.

## v1 target

**iPhone 13 / iPhone 14** size class (146.7 × 71.5 × 7.65 mm).

Why pick a specific model for v1:

- One case mold (3D-print first, injection-mold later) = less
  manufacturing variance
- Easiest to validate antenna clearance against a known phone back
- iPhone 13/14 generation is in the operator's hands today

Future variants: pop-out adapter pattern (case is one piece + phone-
specific cradles) or per-phone shells in PETG/nylon as we expand.

## Case envelope

| Dimension | Target |
|---|---|
| External L × W × H | 152 × 74 × 12 mm (phone + 4 mm case thickness incl. battery) |
| Internal cavity for PCB | ~95 × 50 × 4.5 mm |
| Battery slot | ~50 × 40 × 4 mm flat lipo |
| USB-C passthrough port | aligned with phone Lightning port (iPhone 13/14) |
| Button | one tactile, accessible edge |
| LED | one front-facing pinhole on the back of the case |
| Mascot embossing / decal | low-profile on the back |

## Antenna clearance

ESP32-S3-WROOM-1 has an integrated PCB antenna. To preserve performance:

- Keep ~15 mm of ground-plane-free space adjacent to the antenna edge
- Don't sandwich the antenna between the phone back and a metal sheet
- Orient the WROOM-1 with antenna pointing away from the phone's own
  cellular/WiFi antennas (typically top edge on iPhone)

Concrete: WROOM-1 mounted near the BOTTOM of the case (away from
iPhone's top antenna band), antenna edge facing the case opening.

## Materials

| Component | Material | Why |
|---|---|---|
| Shell (v1 proto) | PETG, 3D-printed | Cheap, iteration-friendly, ESD-OK |
| Shell (v2+) | Injection-molded PC or TPU blend | Production quality, drop resistant |
| Bumper (optional) | Silicone | Drop protection without fully wrapping the case |
| Hardware mounts | M1.6 brass inserts | Cleaner than self-tapping; reduces wear |

## Mockup workflow (M2)

1. CAD the case in OpenSCAD or FreeCAD (parametric, easier to iterate)
2. 3D-print v0 in PETG at 0.16 mm layer height
3. Mount a non-functional dummy PCB (drilled prototype board) for fit
4. Operator validates: phone insertion + retention + USB-C alignment +
   button + LED viewability
5. Iterate, then commit the model to `hardware/case/case.scad` or
   `hardware/case/case.FCStd`

## Branding on the shell

- Mascot embossing on the back (the gorgon head, simplified for relief)
- "MEDUSA" wordmark in HARTLE.TECH typography (Space Grotesk display
  weight, see `assets/brand/brand_tokens.yaml`)
- Optional QR code linking to `medusa.hartle.tech` (small, debossed)
- HARTLE.TECH micro-mark on a side edge — keeps the case identifiable
  (per `design/threat-model.md` — not concealable; openly research)

## Open questions

- [ ] Whether to integrate an external antenna connector (U.FL) for
      higher-gain antennas in audit scenarios — adds complexity, also
      adds antenna swap-ability for specific frequency work
- [ ] Wireless charging compatibility — does Medusa's PCB layout
      tolerate Qi coil under the case?
- [ ] MagSafe interaction — iPhone 13/14 are MagSafe-equipped; we
      should not break compatibility
