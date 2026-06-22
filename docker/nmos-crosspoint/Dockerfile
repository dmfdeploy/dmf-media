# syntax=docker/dockerfile:1
#
# nmos-crosspoint — AMWA NMOS IS-04/05 crosspoint routing UI (3dmedium/nmos_crosspoint).
# Svelte UI + Node/Express(TypeScript) WebSocket server. Built for ARM64 DMF
# media nodes.
#
# Two-stage build (the upstream Dockerfile is single-stage/dev-oriented; we split
# build vs runtime so the image ships only dist + built UI + production deps):
#   build:   clone at a pinned ref, build the UI FIRST (its vite.config.ts writes
#            to ../server/public), then `tsc` the server, then prune dev deps.
#   runtime: copy server/dist + server/public + production node_modules only.
#
# CRITICAL: the Express server serves ./public and falls back to
# ./public/index.html — without server/public the container starts but renders
# nothing. The UI build is what populates it.
#
# Pin policy mirrors nmos-cpp (no floating master for a published DMF image).
# Node 24 matches the upstream Dockerfile; Vite/Rolldown deps require
# ^20.19.0 || >=22.12.0, so a floating node:20 is NOT safe.

ARG NMOS_CROSSPOINT_REF=564ca4120fa233e42ed54582aeba61fdcc5ebab9
ARG IMAGE_VERSION=0.1.0-dev
ARG VCS_REF=unknown

# ── Stage 1: build ───────────────────────────────────────────────────────────
FROM node:24-bookworm AS builder
ARG NMOS_CROSSPOINT_REF

WORKDIR /src
RUN test -n "$NMOS_CROSSPOINT_REF" \
      || (echo "ERROR: NMOS_CROSSPOINT_REF build arg required (no master builds)" >&2 && exit 1) \
    && git clone https://github.com/3dmedium/nmos_crosspoint.git . \
    && git checkout "$NMOS_CROSSPOINT_REF" \
    && git rev-parse HEAD > /src/.crosspoint_ref

# Build-stage resilience patch (dmfdeploy/dmfdeploy#108): upstream registers the
# registry-subscription error handler as `.ws.error = …` — but `ws` has no
# `error` property, so a failed/unreachable ws_href emits an UNHANDLED 'error'
# event and crashes Node (CrashLoopBackOff). Rename to the real WebSocket
# property accessor `.ws.onerror` so the listener is actually registered; the
# existing onclose handler then reconnects. Fail the build if the line moved.
RUN test -f server/src/lib/nmosConnector.ts \
    && sed -i 's/\.ws\.error = ()/.ws.onerror = ()/' server/src/lib/nmosConnector.ts \
    && grep -q '\.ws\.onerror = ()' server/src/lib/nmosConnector.ts

# Build-stage mixed-content patch (dmfdeploy/dmfdeploy#108): the UI picks the
# WebSocket scheme with `window.location.protocol == 'https'`, but that property
# is `'https:'` (trailing colon), so the test is ALWAYS false and the client
# always builds a `ws://` (insecure) URL. Served over HTTPS the browser blocks
# it as mixed content ("connecting…" forever). Fix the colon for both the scheme
# and the default-port comparisons. Fail the build if the lines moved.
RUN test -f ui/src/lib/ServerConnector/ServerConnectorService.ts \
    && sed -i "s/location.protocol == 'https'/location.protocol == 'https:'/g; s/location.protocol == 'http'/location.protocol == 'http:'/g" ui/src/lib/ServerConnector/ServerConnectorService.ts \
    && grep -q "location.protocol == 'https:'" ui/src/lib/ServerConnector/ServerConnectorService.ts \
    && grep -q "location.protocol == 'http:'" ui/src/lib/ServerConnector/ServerConnectorService.ts \
    && ! grep -q "location.protocol == 'https'" ui/src/lib/ServerConnector/ServerConnectorService.ts \
    && ! grep -q "location.protocol == 'http'" ui/src/lib/ServerConnector/ServerConnectorService.ts

# UI first — vite build.outDir = ../server/public (emptyOutDir: true).
# npm install --force per upstream README (peer-dep resolution).
WORKDIR /src/ui
RUN npm install --force \
    && npm run build

# Server — `npm run build` == `tsc` (typescript is a devDependency).
WORKDIR /src/server
RUN npm install \
    && npm run build \
    && npm prune --omit=dev

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM node:24-bookworm-slim AS runtime
ARG IMAGE_VERSION
ARG VCS_REF
LABEL org.opencontainers.image.title="nmos-crosspoint" \
      org.opencontainers.image.description="AMWA NMOS IS-04/05 crosspoint routing UI (3dmedium/nmos_crosspoint)" \
      org.opencontainers.image.source="https://github.com/3dmedium/nmos_crosspoint" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

WORKDIR /app/server
COPY --from=builder /src/server/dist ./dist
COPY --from=builder /src/server/public ./public
COPY --from=builder /src/server/node_modules ./node_modules
COPY --from=builder /src/server/package.json ./package.json
COPY --from=builder /src/.crosspoint_ref /app/.crosspoint_ref

# settings.json + users.json are mounted here by the Helm chart (ConfigMap +
# Secret). state/ is the server's optional persistence dir — keep it writable.
RUN mkdir -p /app/server/config /app/server/state

EXPOSE 80
# Entrypoint runs from /app/server so the server resolves ./config and ./public.
ENTRYPOINT ["node", "./dist/server.js"]
