// Medusa internal wiki theme — same brand tokens as external. Served at
// https://medusa.hartle.tech/wiki/ behind Caddy's remote_ip matcher
// (tailnet-only). Looks identical to the public site by design.

import DefaultTheme from 'vitepress/theme'
import './style.css'

export default DefaultTheme
