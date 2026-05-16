---
title: Medusa — Internal Wiki
---

# 🐍 Medusa — Internal Wiki

Tailnet-only operator wiki. Public-safe content lives under [`docs/external/`](../external/) (served at [medusa.hartle.tech/docs](https://medusa.hartle.tech/docs/)). Anything here may name internal paths, hardware supplier links, in-progress threat modeling, candid technical write-ups — written for future-Claude and future-operator picking the project up cold.

## Where to start

- **Want the technical write-up of the Marauder deauth patch?** Read [research/marauder-deauth-patch](./research/marauder-deauth-patch) (hand-written, dense) + [research/marauder-deauth-patch-swarm](./research/marauder-deauth-patch-swarm) (5-agent swarm cross-check, structured)
- **Want the architecture overview?** [design/architecture](./design/architecture)
- **Want the BOM with supplier links?** [hardware/bom](./hardware/bom)
- **Want the legal frame?** [research/legal-frame](./research/legal-frame)

## Sections

### 🧪 Research
[research/](./research/) — the candid technical write-ups: Marauder patch analysis (both versions), companion-app integration notes, prior-art ecosystem survey, jurisdictional legal frame.

### 🏗 Design
[design/](./design/) — architecture (component layout, ESP-IDF stack), threat model (operator safety guards, in-scope vs out-of-scope), companion API spec (BLE GATT, JSON envelope, op set).

### ⚙️ Hardware
[hardware/](./hardware/) — BOM with supplier links + cost estimates, power budget per operating mode, form-factor + antenna clearance + materials.

## Coordination with the public docs

The two halves are intentionally **complementary**:

- **Public** (medusa.hartle.tech/docs/) — defensive framing, lawful-use anchored, no offensive how-to. Audience: security researchers, network operators auditing their own infrastructure, curious humans.
- **Internal** (this site) — candid technical detail, supplier links, jurisdictional nuance, in-flight threat-modeling. Audience: HARTLE.TECH operators + tailnet-resident reviewers.

When something graduates from internal to public-safe, it gets adapted (lawful-use anchor, defensive framing, no operator names, no internal paths) and pushed to `docs/external/`.

## Cross-references

- **GitHub repo**: [code-hartle-tech/medusa](https://github.com/code-hartle-tech/medusa)
- **Quest board**: [project #8 — 🐍 Medusa Quest Board](https://github.com/orgs/code-hartle-tech/projects/8)
- **Sibling internal wikis**: [void.hartle.tech](https://void.hartle.tech/) (org-wide), DumpSock wiki (tailnet-only at dumpsock.hartle.tech/wiki/)
