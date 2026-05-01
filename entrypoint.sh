#!/bin/sh
set -e

# Create required directories with restrictive permissions
mkdir -p /config /downloads
chmod 700 /config
chmod 750 /downloads

# Warn if rclone config is missing
if [ ! -f "$RCLONE_CONFIG_PATH" ]; then
    echo "[WARN] No rclone config found at $RCLONE_CONFIG_PATH."
    echo "[WARN] Upload one via the bot (/set_rclone_conf) or mount at that path."
fi

# Enforce restrictive permissions on existing rclone config
if [ -f "$RCLONE_CONFIG_PATH" ]; then
    chmod 600 "$RCLONE_CONFIG_PATH"
fi

# Require ALLOWED_USER_IDS to be set in production
if [ -z "$ALLOWED_USER_IDS" ]; then
    echo "[WARN] ALLOWED_USER_IDS is not set — bot is open to ALL Telegram users!"
fi

exec python bot.py
