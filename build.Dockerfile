FROM artifactory.nike.com:9002/fci/nike-debian-slim-python3-foundation:12.2.0-438

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3-venv \
    rm -rf /var/lib/apt/lists/*