FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml setup.py README.md 固定业务规则 ./
COPY src ./src
COPY web ./web

RUN pip install --no-cache-dir .
RUN mkdir -p /app/.state /app/logs

EXPOSE 8000

CMD ["requirement-monitor", "api", "--database-url", "postgresql+psycopg://monitor:monitor@db:5432/requirement_monitor", "--host", "0.0.0.0", "--port", "8000"]
