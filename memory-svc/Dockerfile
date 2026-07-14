# Portable agent memory sidecar: a small Node service that keeps each user's
# encrypted memory on Walrus and exposes remember / recall over HTTP for the
# app to call internally.

FROM node:20-slim

ENV NODE_ENV=production
WORKDIR /svc

# Install dependencies first (the vendored Avow SDK tarball plus its transitive
# Sui/Walrus deps), so this layer caches until the manifest or vendor changes.
COPY package.json ./
COPY vendor ./vendor
RUN npm install --omit=dev

COPY server.mjs ./

# Run as a non-root user.
RUN useradd --create-home --uid 10002 svcuser
USER svcuser

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:4000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.mjs"]
