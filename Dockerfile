FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM node:22-bookworm-slim AS codex
RUN npm install --global @openai/codex@0.147.0 \
    && find /usr/local/lib/node_modules/@openai/codex -type f -path '*/bin/codex' -exec cp '{}' /codex-bin \;

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml setup.py README.md ./
COPY src ./src
COPY --from=frontend /build/web ./web
COPY --from=codex /codex-bin /usr/local/bin/codex
RUN pip install --no-cache-dir . \
    && mkdir -p /app/.state /app/logs /root/.codex
EXPOSE 8000
CMD ["requirement-monitor", "api", "--host", "0.0.0.0", "--port", "8000"]
