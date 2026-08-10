# SvelteKit static build — output copied into the Python stage below and served by FastAPI
# (see FRONTEND_DIST in app/main.py). Only frontend/ is needed here; the Python stage never
# gets a Node runtime.
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
# --legacy-peer-deps: openapi-typescript's peer range (typescript ^5.x) is behind the project's
# actual typescript version — see frontend/package.json; harmless since it's a codegen CLI tool,
# not something that needs to interoperate with the app's own TS at runtime.
RUN npm ci --legacy-peer-deps
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=frontend-build /frontend/build ./frontend-dist

RUN useradd --create-home appuser
ENV TBR_DB_PATH=/data/tbr.db
RUN mkdir -p /data && chown appuser:appuser /data
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
