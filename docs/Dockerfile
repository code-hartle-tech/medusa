# medusa docs container — multi-stage.
# Stage 1 builds both VitePress halves; stage 2 wraps the chosen variant
# in caddy:2-alpine. VARIANT build-arg picks {external, internal, landing}.

ARG VARIANT=external

# ── build stage ──────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /src

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run external:build && npm run internal:build
# Landing is static HTML in landing/ — no build step.

# ── runtime stage ────────────────────────────────────────────────────
FROM caddy:2-alpine AS runtime
ARG VARIANT
WORKDIR /srv

# external + internal → .vitepress/dist; landing → directly.
COPY --from=build /src/${VARIANT}/.vitepress/dist /srv-vp
COPY --from=build /src/landing /srv-landing
# Pick whichever exists. `tools.sh` trick: use a shell conditional via RUN.
RUN if [ "${VARIANT}" = "landing" ]; then cp -r /srv-landing/. /srv/; else cp -r /srv-vp/. /srv/; fi
COPY Caddyfile /etc/caddy/Caddyfile

USER nobody
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=2s \
  CMD wget --quiet --spider http://127.0.0.1:80/ || exit 1

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
