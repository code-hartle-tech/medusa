# medusa docs container — multi-stage.
# VARIANT build-arg picks {external, internal, landing}.

ARG VARIANT=external

# ── build stage ──────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /src

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run external:build && npm run internal:build

# Normalize all three variants into /staging/<variant>/ so the runtime
# COPY can pick one with a single line, regardless of build vs static.
RUN mkdir -p /staging/external /staging/internal /staging/landing && \
    cp -r external/.vitepress/dist/. /staging/external/ && \
    cp -r internal/.vitepress/dist/. /staging/internal/ && \
    cp -r landing/. /staging/landing/

# ── runtime stage ────────────────────────────────────────────────────
FROM caddy:2-alpine AS runtime
ARG VARIANT
WORKDIR /srv

COPY --from=build /staging/${VARIANT}/ /srv/
COPY Caddyfile /etc/caddy/Caddyfile

USER nobody
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=2s \
  CMD wget --quiet --spider http://127.0.0.1:8080/ || exit 1

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
