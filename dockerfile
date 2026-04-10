FROM debian:bookworm-slim

# Install all runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    python3 \
    python3-pip \
    openssl \
    sqlite3 \
    iproute2 \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Install Flask (Werkzeug is bundled with Flask)
RUN pip3 install flask --break-system-packages

# Application directory
RUN mkdir -p /opt/kme/certs

# Copy the KME application
COPY kme_server.py /opt/kme/kme_server.py

# Copy server-side certificates (built by the CA on the host before docker build)
# Only the server identity and the CA for client validation go into the image.
# SAE client certificates are delivered separately to the routers.
COPY certs/ca.crt         /opt/kme/certs/ca.crt
COPY certs/kme-server.crt /opt/kme/certs/kme-server.crt
COPY certs/kme-server.key /opt/kme/certs/kme-server.key

# Expose the KME API port
EXPOSE 8020

# Default command
CMD ["python3", "/opt/kme/kme_server.py"]
