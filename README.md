```markdown
# Rclone Cloud Archive Automation ☁️
An automated solution for syncing and archiving data across multiple cloud storage providers using the power of Rclone and Python.

## 🚀 Key Features
* **Multi-Cloud Sync:** Leveraging Rclone to support over 40+ cloud storage providers.
* **Automated Workflows:** Python-based logic to trigger sync/archive tasks efficiently.
* **Scalable & Portable:** Designed to run in Docker containers for consistent performance.
* **Secure:** Sensitive data and remote configurations are handled via environment variables and encrypted config files.

## 🛠 Tech Stack
* **Core Tool:** Rclone
* **Scripting:** Python
* **Environment:** Dockerized for easy setup
* **Support:** Works with S3, Google Drive, Dropbox, OneDrive, and more.

## 🚀 Quick Start
1. **Prepare Rclone Config:** Ensure your `rclone.conf` is ready.
2. **Environment Setup:** Set your remote paths and sync intervals in the `.env` file.
3. **Deploy:**
   ```bash
   docker build -t archive-rclone .
   docker run --env-file .env archive-rclone
# Archive.org → (rclone) Telegram Bot (Bot API Version)

This bot uses **Telegram Bot API** (Bot Token), not user session.  
It can be safely deployed with just a Bot Token, API ID, and API Hash.

## Features
- Accepts `/download <archive.org link>` commands.
- Fetches metadata from archive.org and lists available files (formats).
- User can pick file via inline buttons.
- Downloads file to server, uploads to OneDrive Business using `rclone`.
- Uploads your `rclone.conf` file via `/set_rclone_conf`.
- Cleans up temporary files after upload.

## Environment Variables
- `BOT_TOKEN` — Telegram bot token (from @BotFather)
- `API_ID` — Telegram API ID (from my.telegram.org)
- `API_HASH` — Telegram API Hash
- `RCLONE_CONFIG_PATH` — path to rclone config file (default `/config/rclone.conf`)
- `TEMP_DOWNLOAD_DIR` — path to temp downloads (default `/downloads`)

## Deployment (Railway)
1. Create a new Railway project.
2. Add your secrets (`BOT_TOKEN`, `API_ID`, `API_HASH`, ...).
3. Deploy directly from this repo (Dockerfile included).
4. Use `/set_rclone_conf` to upload your rclone.conf file, or mount one into `/config/rclone.conf`.

