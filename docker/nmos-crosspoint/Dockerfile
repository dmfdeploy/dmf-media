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
