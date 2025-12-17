# Multi-stage build for smaller final image
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt


# Final stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY api/ /app/api/
COPY apps/ /app/apps/
COPY examples/ /app/examples/
COPY launcher/ /app/launcher/
COPY requirements.txt /app/

# Copy entrypoint script and fix line endings (Windows -> Unix)
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Configure jupyter_app_launcher
# Config must be in jupyter_app_launcher subdirectory of jupyter data dir
RUN mkdir -p /root/.local/share/jupyter/jupyter_app_launcher && \
    mkdir -p /root/.jupyter

# Copy launcher config to correct location
RUN cp /app/launcher/jp_app_launcher.yaml /root/.local/share/jupyter/jupyter_app_launcher/jp_app_launcher.yaml

# Expose ports: FastAPI (8000) and JupyterLab (8888)
EXPOSE 8000 8888

# Health check on FastAPI
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Environment variables
ENV JUPYTER_TOKEN=""
ENV API_HOST="0.0.0.0"
ENV API_PORT="8000"

# Run both JupyterLab and FastAPI
CMD ["/app/entrypoint.sh"]
