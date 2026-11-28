# Blog Writer v2: one image, one process (plan: "Target architecture" > "Runtime").
#
# Build it remotely from the repository root (no local Docker needed):
#   az acr build --registry <acr> --resource-group <acr-rg> --platform linux/amd64 \
#     --image blog-writer-v2:<sha> --build-arg BUILD_SHA=<sha> .
# or run `scripts/deploy-v2.sh build`. The build context is an allowlist (.dockerignore).
#
# The file works with BuildKit and with the classic builder, because ACR Tasks may run either:
# no RUN --mount, COPY --chmod/--link, heredocs or syntax directive.
#
# Base images are pinned to their multi-arch index digests (Renovate keeps them current).
# Resolved 2026-11-26 from Docker Hub and GHCR:
#   python:3.12.16-slim-bookworm  sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64
#                                 (the same digest as python:3.12-slim-bookworm then)
#   node:24.22.3-bookworm-slim    sha256:873f586f3794f52281bd039bf02c048a0be08fde619338f9941bad1eb6b34530
#                                 (the same digest as node:24-bookworm-slim; CI pins Node 24.22.3 too)
#   ghcr.io/astral-sh/uv:0.12.5   sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1
#                                 (the uv version CI uses; the image holds only /uv and /uvx)

FROM ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 AS uv

FROM node:24.22.3-bookworm-slim@sha256:873f586f3794f52281bd039bf02c048a0be08fde619338f9941bad1eb6b34530 AS node-base

FROM python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64 AS python-base


# --- SPA: tsc -b, vite build and scripts/verify-dist.mjs (npm run build) --------------------------
# src/lib/api/schema.ts is committed, so gen:api is not run here. No package needs an install
# script on Linux (only the macOS-only fsevents has one), so npm ci runs with --ignore-scripts.
FROM node-base AS web-build
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
# The SPA shows the build SHA (vite.config.ts reads BW_BUILD_SHA). Declared after npm ci so the
# dependency layer stays cached from one commit to the next.
ARG BUILD_SHA=dev
RUN BW_BUILD_SHA="${BUILD_SHA}" npm run build


# --- Mermaid browser bundle for blogwriter.render (server/vendor: npm, @mermaid-js/tiny) ----------
# npm ci checks the lockfile's integrity hashes; the renderer pins the bundle's SHA-256 itself.
FROM node-base AS vendor-build
WORKDIR /src/server/vendor
COPY server/vendor/package.json server/vendor/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund \
 && test -s node_modules/@mermaid-js/tiny/dist/mermaid.tiny.js


# --- Server: locked dependencies into /app/.venv, then the project as a regular (non-editable) wheel
# The venv is built at the path it runs from, on the same Python (the runtime stage shares this
# base), so its interpreter link, script shebangs and precompiled bytecode stay valid.
FROM python-base AS server-build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src/server
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --locked --no-dev --no-editable --no-install-project
# README.md is the package's readme in pyproject.toml, so the wheel build needs it.
COPY server/README.md ./
COPY server/src ./src
RUN uv sync --locked --no-dev --no-editable


# --- Runtime ----------------------------------------------------------------------------------------
FROM python-base AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# tini is PID 1: it forwards SIGTERM to uvicorn and reaps Chromium's child processes.
# libatomic1: the Playwright driver's bundled node binary links against it (slim lacks it).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tini libatomic1; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --gid 10001 app; \
    useradd --uid 10001 --gid app --no-log-init --create-home --home-dir /home/app \
        --shell /usr/sbin/nologin app

COPY --from=server-build /app/.venv /app/.venv

# Chrome Headless Shell for the Playwright version pinned in uv.lock, plus the OS libraries and
# fonts Playwright lists for it. Runs as root (apt); the app user only reads /ms-playwright.
RUN set -eux; \
    DEBIAN_FRONTEND=noninteractive python -m playwright install --with-deps --only-shell \
        --no-progress chromium; \
    chmod -R a+rX /ms-playwright; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /root/.cache /tmp/*

COPY --from=web-build /src/web/dist /app/web
COPY --from=vendor-build /src/server/vendor/node_modules/@mermaid-js/tiny/dist/mermaid.tiny.js \
     /app/vendor/mermaid.tiny.js
# blogwriter.publish.contract reads these two files at runtime (BW_PUBLISH_CONTRACTS_DIR). The
# installed package cannot find the repository's contracts/ directory by walking up from itself.
COPY contracts/published-blogs.v2.schema.json contracts/sanitizer-profile.json /app/contracts/

ENV BW_SPA_DIR=/app/web \
    BW_RENDER_MERMAID_JS=/app/vendor/mermaid.tiny.js \
    BW_PUBLISH_CONTRACTS_DIR=/app/contracts

# Last, so a new SHA does not invalidate the layers above.
ARG BUILD_SHA=dev
ENV BW_BUILD_SHA=${BUILD_SHA}
LABEL org.opencontainers.image.title="blog-writer-v2" \
      org.opencontainers.image.revision="${BUILD_SHA}"

WORKDIR /app
USER 10001:10001
EXPOSE 8080

# uvicorn flags:
# * --no-proxy-headers: uvicorn trusts X-Forwarded-For/-Proto from 127.0.0.1 by default, which
#   inside an ACA replica can be a co-located sidecar. The app needs neither header: it never builds
#   URLs or redirects from the request's scheme or host (redirect_slashes is off), the CSRF check
#   compares Origin with the configured BW_PUBLIC_ORIGIN, Host is only checked for the dev
#   principal (BW_ENV=local), HSTS depends on BW_ENV, cookies are always Secure, and logs never
#   record the client address. Ignoring forwarded headers keeps all of that fail-closed, so
#   --forwarded-allow-ips is not needed either.
# * --lifespan on: the app cannot serve without its lifespan (store, jobs, watchdog). "auto" would
#   keep serving if a middleware ever broke the lifespan protocol; "on" stops the process instead.
# * One worker (the default): the app is one process with its own job runner and scheduler.
# tini -e 143: after a graceful shutdown uvicorn re-raises the SIGTERM it caught, so a normal stop
# would end with 143. Mapping it to 0 leaves only real failures (and SIGKILL, 137) non-zero.
ENTRYPOINT ["/usr/bin/tini", "-e", "143", "--"]
CMD ["uvicorn", "blogwriter.main:app", "--factory", "--host", "0.0.0.0", "--port", "8080", \
     "--no-access-log", "--no-server-header", "--timeout-graceful-shutdown", "15", \
     "--no-proxy-headers", "--lifespan", "on"]
