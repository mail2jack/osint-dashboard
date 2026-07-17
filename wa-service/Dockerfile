FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci --omit=dev && \
    rm -rf /root/.npm /tmp/*

COPY index.js .

RUN adduser -D appuser && \
    mkdir -p auth && \
    chown -R appuser:appuser /app

USER appuser

ENV NODE_ENV=production
ENV PORT=3001
ENV AUTH_DIR=/app/auth
ENV LOG_LEVEL=info

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD wget -qO- http://localhost:3001/health || exit 1

EXPOSE 3001

CMD ["node", "index.js"]
