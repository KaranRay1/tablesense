# TableSense images.
#   dashboard  web app + alerts only (small, standard library + pyyaml)
#   pipeline   camera -> people detection -> event log (adds OpenCV + YOLO, CPU)
# Build one with:  docker build --target dashboard -t tablesense-dashboard .
# Usually you don't build by hand: `docker compose up -d` does it (see docker-compose.yml).

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 tablesense
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .
# Event log, references and model weights live in mounted volumes, never in the image.
RUN mkdir -p /app/data /app/models /app/configs && chown -R tablesense /app/data /app/models

FROM base AS dashboard
USER tablesense
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["python", "-m", "tablesense.dashboard.server"]

FROM base AS pipeline
# OpenCV's runtime libraries, then CPU-only PyTorch (the default wheel bundles
# gigabytes of CUDA a venue box can't use), then the vision extras.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install ".[cv]"
USER tablesense
ENTRYPOINT ["python", "-m", "tablesense.cli"]
