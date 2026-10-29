# =============================================================================
# Cybotech Frontend — Dockerfile
# Development image: runs the Vite dev server with HMR.
# For a production static build, add a second stage using `pnpm build`
# and serve with nginx or a static file server.
# =============================================================================

FROM node:20-slim AS base

# Install pnpm via corepack (ships with Node 20)
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

WORKDIR /app

# ---------------------------------------------------------------------------
# Dependency install stage
# Cached unless package.json or pnpm-lock.yaml changes
# ---------------------------------------------------------------------------
FROM base AS deps

COPY package.json pnpm-lock.yaml* ./

# Install all dependencies (including devDeps for the dev server)
RUN pnpm install --frozen-lockfile

# ---------------------------------------------------------------------------
# Development server stage
# ---------------------------------------------------------------------------
FROM base AS dev

WORKDIR /app

# Copy installed modules from deps stage
COPY --from=deps /app/node_modules ./node_modules

# Copy source (hot-reloaded from the volume mount in compose.yaml dev workflow)
COPY . .

EXPOSE 5173

# Vite dev server — host 0.0.0.0 so it's reachable from Docker host
CMD ["pnpm", "dev", "--host", "0.0.0.0", "--port", "5173"]
