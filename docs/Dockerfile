# medusa docs container — multi-stage.
# VARIANT build-arg picks {external, internal, landing}.
#
# Build context is the REPOSITORY ROOT, not docs/. The public site lives in
# docs/external and the private wiki in the top-level wiki/ realm (the
# 2026-05-23 naming convention moved docs/internal → wiki), so a docs-only
# context cannot see the internal variant's source at all.

ARG VARIANT=external

# ── build stage ──────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /src

# Copy the whole (already .dockerignore-filtered) context rather than naming
# wiki/ explicitly. The public GitHub mirror deliberately omits wiki/ — it is
# the tailnet-only private realm — and a `COPY wiki/ …` there fails the build
# for every variant, including the two that do not need it.
COPY . ./

RUN cd docs && npm ci --no-audit --no-fund && npm run external:build

# The internal variant is the private wiki. It can only be built where that
# realm exists, which means Forgejo and not the public mirror.
RUN if [ -d wiki ]; then \
      cd wiki && npm ci --no-audit --no-fund && npm run build; \
    else \
      echo "note: wiki/ absent from this context — the internal variant cannot be built here"; \
    fi

# Normalize the buildable variants into /staging/<variant>/ so the runtime
# COPY can pick one with a single line, regardless of build vs static.
RUN mkdir -p /staging/external /staging/landing && \
    cp -r docs/external/.vitepress/dist/. /staging/external/ && \
    cp -r docs/landing/. /staging/landing/ && \
    if [ -d wiki/.vitepress/dist ]; then \
      mkdir -p /staging/internal && cp -r wiki/.vitepress/dist/. /staging/internal/; \
    fi

# Fail loudly instead of shipping an empty site. A VitePress build against a
# missing source root still exits 0 and still emits assets + 404.html; only the
# absence of index.html distinguishes it from a real build. This assertion is
# what turns that silent degradation into a red build.
#
# Asking for the internal variant without wiki/ is a wiring error, not a
# tolerable degradation, so it fails here with a message that says why.
ARG VARIANT
RUN if [ "${VARIANT}" = "internal" ] && [ ! -d /staging/internal ]; then \
      echo "FATAL: VARIANT=internal needs the private wiki/ realm, which is absent from this build context."; \
      echo "       Build the internal variant from Forgejo, not from the public GitHub mirror."; \
      exit 1; \
    fi; \
    for v in /staging/*; do \
      test -s "$v/index.html" || { \
        echo "FATAL: $v/index.html is missing or empty — refusing to ship an empty site"; \
        exit 1; \
      }; \
    done

# ── runtime stage ────────────────────────────────────────────────────
FROM caddy:2-alpine AS runtime
# Drop CAP_NET_BIND_SERVICE fcap from /usr/bin/caddy. With our
# securityContext (drop:[ALL] + allowPrivilegeEscalation:false +
# runAsNonRoot:true), fcap-bearing binaries fail exec with EPERM
# under NoNewPrivs. Stripping the fcap is the cleanest fix; we
# listen on :8080 anyway, no low-port bind needed.
USER root
RUN setcap -r /usr/bin/caddy || true
ARG VARIANT
WORKDIR /srv

COPY --from=build /staging/${VARIANT}/ /srv/
COPY docs/Caddyfile /etc/caddy/Caddyfile

USER nobody
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=2s \
  CMD wget --quiet --spider http://127.0.0.1:8080/ || exit 1

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
