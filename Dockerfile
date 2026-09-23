FROM python:3.12-slim

WORKDIR /app

# Keep the image small and boring; no build-time credentials
COPY pyproject.toml README.md ./
COPY src ./src
COPY schemas ./schemas
COPY benchmark ./benchmark
COPY datasets ./datasets
COPY examples ./examples
COPY scripts ./scripts
COPY results ./results
COPY docs ./docs

RUN pip install --no-cache-dir -e . && \
    python3 scripts/generate_dataset.py --check

ENTRYPOINT ["jev-monitor"]