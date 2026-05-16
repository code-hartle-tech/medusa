import { defineConfig } from 'vitepress'

// Medusa internal wiki — served at https://medusa.hartle.tech/wiki/ behind
// Caddy on the OVH VPS. Caddy's remote_ip matcher enforces tailnet-only
// access for this path (CGNAT 100.64.0.0/10 + fd7a:115c:a1e0::/48).
// Public internet hits /wiki/ → 404. Looks identical to /docs/ by design.

export default defineConfig({
  title: 'Medusa — Wiki',
  description: 'Tailnet-only internal wiki for the Medusa project. Research, design, hardware notes.',
  lang: 'en-US',
  cleanUrls: true,
  // Served at https://private.medusa.hartle.tech/ via Caddy on the VPS.
  // DNS A record points at the tailnet IP (100.105.94.108) — the DNS
  // itself is the access boundary, like void.hartle.tech. No Caddy
  // remote_ip gate; off-tailnet clients can't resolve / route here.
  base: '/',
  appearance: false,

  // Cross-site refs to ../external/*, ../ (repo root) won't resolve from
  // a build of this site alone. Tolerate.
  ignoreDeadLinks: [
    /external\//,
    /^\.\.\//,
    /^https?:\/\/localhost/,
  ],

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/wiki/medusa-mascot.svg' }],
    ['meta', { name: 'theme-color', content: '#2DAB66' }],
    // Access control is enforced by Caddy at the network layer. noindex is
    // belt-and-suspenders so any tailnet member sharing a link doesn't
    // accidentally trip a crawler.
    ['meta', { name: 'robots', content: 'noindex,nofollow' }],
  ],

  themeConfig: {
    siteTitle: 'Medusa — Wiki',

    nav: [
      { text: 'Research', link: '/research/' },
      { text: 'Design', link: '/design/' },
      { text: 'Hardware', link: '/hardware/' },
      { text: '↗ Public docs', link: 'https://medusa.hartle.tech/docs/', target: '_blank' },
      { text: '↗ GitHub', link: 'https://github.com/code-hartle-tech/medusa', target: '_blank' },
    ],

    sidebar: {
      '/': [
        {
          text: '🐍 Start',
          items: [
            { text: 'About this wiki', link: '/' },
          ],
        },
        {
          text: '🧪 Research',
          items: [
            { text: 'Index', link: '/research/' },
            { text: 'Marauder deauth patch (hand-written)', link: '/research/marauder-deauth-patch' },
            { text: 'Marauder deauth patch (swarm cross-check)', link: '/research/marauder-deauth-patch-swarm' },
            { text: 'Companion-app integration', link: '/research/companion' },
            { text: 'Pentest ecosystem prior art', link: '/research/ecosystem' },
            { text: 'Legal frame (EU/US/PT)', link: '/research/legal-frame' },
          ],
        },
        {
          text: '🏗 Design',
          items: [
            { text: 'Index', link: '/design/' },
            { text: 'Architecture', link: '/design/architecture' },
            { text: 'Threat model', link: '/design/threat-model' },
            { text: 'Companion API spec', link: '/design/api-spec' },
          ],
        },
        {
          text: '⚙️ Hardware',
          items: [
            { text: 'Index', link: '/hardware/' },
            { text: 'Bill of materials', link: '/hardware/bom' },
            { text: 'Power budget', link: '/hardware/power-budget' },
            { text: 'Form factor', link: '/hardware/form-factor' },
          ],
        },
      ],
    },

    footer: {
      message: 'Internal · Tailnet-only · <a href="/docs/">back to the public site</a>',
      copyright: '© HARTLE.TECH',
    },

    search: { provider: 'local' },
  },
})
