# UI build
FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# API + static
FROM python:3.12-slim
# ffmpeg is not optional: poster frames and the supercut are both ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py agent.py ingest.py verify.py queries.cypher ./
# the clips ship with the image — without them every player is a 404 and the proof is gone
COPY data/ ./data/
COPY cache/ ./cache/
COPY --from=ui /ui/dist ./ui/dist
ENV OTEL_SDK_DISABLED=true
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
