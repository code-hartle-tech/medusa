import { defineConfig } from 'vitepress'

// Medusa public docs — served at https://medusa.hartle.tech/docs/ via Caddy
// on the OVH VPS. The Ansible role medusa_docs clones this repo, runs
// npm run external:build, and Caddy file-servers the dist tree behind
// path /docs/. Internal wiki lives at /wiki/ (tailnet-only) — see
// docs/internal/.vitepress/config.ts.

export default defineConfig({
  title: 'Medusa',
  description: 'Embedded wireless-recon research — ESP32-S3 in a phone case, with a phone for UI. A HARTLE.TECH project.',
  lang: 'en-US',
  cleanUrls: true,
  base: '/docs/',
  // Brand spec v2 (assets/brand/brand_tokens.yaml v2, 2026-05-16):
  // dark-only — venom-snake palette on charcoal. Replaces the earlier
  // light-only v1 direction.
  appearance: 'force-dark',

  // Cross-references to /wiki/* (the internal site, separate build) won't
  // resolve from a build of /docs/ alone — those links are intentional
  // operator-facing pointers. Tolerate.
  ignoreDeadLinks: [
    /^\/wiki\//,
    /^https?:\/\/localhost/,
  ],

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/docs/medusa-mascot.svg' }],
    ['meta', { name: 'theme-color', content: '#2C2B30' }],
    ['meta', { property: 'og:title', content: 'Medusa — Embedded wireless recon' }],
    ['meta', { property: 'og:description', content: 'A defensive research tool for auditing the radio surface of networks you own. HARTLE.TECH.' }],
  ],

  themeConfig: {
    siteTitle: 'Medusa',

    nav: [
      { text: 'Guide', link: '/guide/getting-started' },
      { text: 'Features', link: '/features/' },
      { text: 'Lawful use', link: '/lawful-use' },
      { text: 'FAQ', link: '/faq' },
      { text: 'Source', link: 'https://github.com/code-hartle-tech/medusa' },
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'Get started',
          items: [
            { text: 'Getting started', link: '/guide/getting-started' },
            { text: 'Hardware overview', link: '/guide/hardware' },
            { text: 'Companion app', link: '/guide/companion' },
          ],
        },
      ],
      '/features/': [
        {
          text: 'Capabilities',
          items: [
            { text: 'Overview', link: '/features/' },
            { text: 'WiFi reconnaissance', link: '/features/wifi-recon' },
            { text: 'BLE inventory', link: '/features/ble' },
            { text: 'Companion-app UI', link: '/features/companion-ui' },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/code-hartle-tech/medusa' },
    ],

    footer: {
      message: 'A <a href="https://hartle.tech">HARTLE.TECH</a> research project · Apache 2.0 · <a href="mailto:contact@hartle.tech">contact@hartle.tech</a>',
      copyright: '© HARTLE.TECH',
    },

    search: { provider: 'local' },
  },
})
