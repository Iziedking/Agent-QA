# The x402 paid lane: a small Node service that takes payment on X Layer through
# the OKX facilitator and forwards the paid call to the app internally.

FROM node:20-slim

ENV NODE_ENV=production
WORKDIR /svc

# Install dependencies first (the OKX x402 packages and Express), so this layer
# caches until the manifest changes.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY server.mjs ./

# Run as a non-root user. This service holds the OKX API credentials in its
# environment and writes nothing to disk, so it needs no data directory.
RUN useradd --create-home --uid 10003 svcuser
USER svcuser

EXPOSE 9100

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:9100/x402/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.mjs"]
