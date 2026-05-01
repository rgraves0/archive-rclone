# ── Base ───────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── System dependencies ────────────────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl unzip ca-certificates git ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ── Install latest rclone (AMD64) ─────────────────────────────────────────────
RUN curl -fsSLo /tmp/rclone.zip \
        https://downloads.rclone.org/rclone-current-linux-amd64.zip && \
    unzip /tmp/rclone.zip -d /tmp && \
    cp /tmp/rclone-*-linux-amd64/rclone /usr/bin/rclone && \
    chown root:root /usr/bin/rclone && \
    chmod 755 /usr/bin/rclone && \
    rm -rf /tmp/rclone*

# ── Non-root user ──────────────────────────────────────────────────────────────
# Running as root inside a container is a security anti-pattern.
# All bot files and runtime dirs are owned by this user.
RUN groupadd -r botuser && useradd -r -g botuser -m botuser

# ── App files ──────────────────────────────────────────────────────────────────
WORKDIR /app
COPY --chown=botuser:botuser . /app

RUN chmod +x /app/entrypoint.sh

# ── Python dependencies ────────────────────────────────────────────────────────
RUN pip install --no-cache-dir -r requirements.txt

# ── Runtime directories ────────────────────────────────────────────────────────
RUN mkdir -p /config /downloads && \
    chown -R botuser:botuser /config /downloads && \
    chmod 700 /config && \
    chmod 750 /downloads

# ── Volumes ────────────────────────────────────────────────────────────────────
VOLUME ["/config", "/downloads"]

# ── Environment defaults ───────────────────────────────────────────────────────
ENV RCLONE_CONFIG_PATH=/config/rclone.conf \
    TEMP_DOWNLOAD_DIR=/downloads \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user before running
USER botuser

# ── Entrypoint ─────────────────────────────────────────────────────────────────
ENTRYPOINT ["/app/entrypoint.sh"]
