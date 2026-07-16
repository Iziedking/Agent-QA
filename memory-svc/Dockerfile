# Portable agent memory sidecar: a small Node service that keeps each user's
# encrypted memory on Walrus and exposes remember / recall over HTTP for the
# app to call internally.

FROM node:20-slim

ENV NODE_ENV=production
WORKDIR /svc

# Install dependencies first (the MemWal client and its Sui/Walrus deps), so
# this layer caches until the manifest changes.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# Copy every source module, not just the entrypoint, or an import of a sibling
# file crash-loops the container.
COPY server.mjs walrus-files.mjs zerog-files.mjs localstore.mjs ./

# Local fallback store lives here; mount a volume at this path to persist it.
ENV AGENT_MEMORY_DATA_DIR=/svc/data

# Run as a non-root user, owning the data directory it writes to.
RUN useradd --create-home --uid 10002 svcuser \
    && mkdir -p /svc/data && chown -R svcuser:svcuser /svc/data
USER svcuser

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:4000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.mjs"]
