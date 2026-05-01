
import os
import logging
import asyncio
import shutil
import pathlib
import requests
from urllib.parse import quote
from collections import defaultdict
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from archive_scraper import parse_archive_url, fetch_metadata, list_files_from_metadata
from uploader import rclone_copy, rclone_list_remotes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Environment ────────────────────────────────────────────────────────────────
API_ID             = int(os.environ["API_ID"])
API_HASH           = os.environ["API_HASH"]
BOT_TOKEN          = os.environ["BOT_TOKEN"]
TEMP_DIR           = pathlib.Path(os.environ.get("TEMP_DOWNLOAD_DIR", "/downloads")).resolve()
RCLONE_CONFIG_PATH = os.environ.get("RCLONE_CONFIG_PATH", "/config/rclone.conf")

# ── Security config ────────────────────────────────────────────────────────────
# Comma-separated Telegram user IDs allowed to use this bot.
# Example:  ALLOWED_USER_IDS=123456789,987654321
_raw_ids = os.environ.get("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS: set[int] = {
    int(uid.strip()) for uid in _raw_ids.split(",") if uid.strip().isdigit()
}

MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(2 * 1024 ** 3)))  # default 2 GB
ALLOWED_ARCHIVE_HOST = "archive.org"

TEMP_DIR.mkdir(parents=True, exist_ok=True)

app = Client(
    "archive_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# In-memory job store (chat_id:msg_id → job dict)
JOBS: dict[str, dict] = {}


# ── Authorization decorator ────────────────────────────────────────────────────
def authorized(func):
    """Reject any user not in ALLOWED_USER_IDS."""
    async def wrapper(client, update):
        user_id = (
            update.from_user.id
            if hasattr(update, "from_user")
            else update.message.from_user.id
        )
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            logger.warning("Unauthorized access attempt from user_id=%s", user_id)
            if hasattr(update, "answer"):          # callback query
                await update.answer("⛔ Not authorized.", show_alert=True)
            else:
                await update.reply_text("⛔ You are not authorized to use this bot.")
            return
        return await func(client, update)
    wrapper.__name__ = func.__name__
    return wrapper


# ── Path-safety helper ─────────────────────────────────────────────────────────
def safe_child_path(base: pathlib.Path, untrusted_name: str) -> pathlib.Path:
    """
    Resolve the joined path and assert it stays inside *base*.
    Raises ValueError on path-traversal attempts.
    """
    candidate = (base / untrusted_name).resolve()
    if not str(candidate).startswith(str(base)):
        raise ValueError(f"Path traversal detected: {untrusted_name!r}")
    return candidate


# ── URL validation ─────────────────────────────────────────────────────────────
def validate_archive_url(url: str) -> str:
    """
    Ensure the URL points to archive.org only (SSRF mitigation).
    Returns the sanitized URL or raises ValueError.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.netloc.lower().lstrip("www.")
    if parsed.scheme not in ("http", "https") or not host.endswith(ALLOWED_ARCHIVE_HOST):
        raise ValueError(f"URL not allowed: {url!r}")
    return url


# ── Streaming download with size guard ────────────────────────────────────────
def stream_download(url: str, dest: pathlib.Path, max_bytes: int = MAX_FILE_BYTES) -> None:
    """Download *url* to *dest*, raising RuntimeError if size exceeds *max_bytes*."""
    validate_archive_url(url)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()

        content_length = r.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise RuntimeError(
                f"File too large ({int(content_length) / 1024**3:.1f} GB > "
                f"{max_bytes / 1024**3:.1f} GB limit)"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    written += len(chunk)
                    if written > max_bytes:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        raise RuntimeError(
                            f"File exceeded size limit of "
                            f"{max_bytes / 1024**3:.1f} GB — download aborted."
                        )
                    fh.write(chunk)


# ── Handlers ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command("start"))
@authorized
async def start_cmd(client, message):
    await message.reply_text(
        "Hello! Send /download <archive.org link> to begin.\n"
        f"Max file size: {MAX_FILE_BYTES / 1024**3:.1f} GB"
    )


@app.on_message(filters.command("download"))
@authorized
async def download_cmd(client, message):
    if len(message.command) < 2:
        await message.reply_text(
            "Usage: /download https://archive.org/details/<identifier>"
        )
        return

    url = message.command[1]
    try:
        validate_archive_url(url)
    except ValueError as exc:
        await message.reply_text(f"Invalid URL: {exc}")
        return

    ident = parse_archive_url(url)
    if not ident:
        await message.reply_text("Could not parse identifier.")
        return

    msg = await message.reply_text(f"Fetching metadata for: {ident} …")
    try:
        meta  = fetch_metadata(ident)
        files = list_files_from_metadata(meta)
        if not files:
            await msg.edit("No downloadable files found.")
            return

        jobid = f"{message.chat.id}:{message.id}"
        JOBS[jobid] = {"identifier": ident, "files": files, "meta": meta}

        format_counts: dict[str, int]         = defaultdict(int)
        format_files:  dict[str, list]        = defaultdict(list)
        for f in files:
            fmt = f["format"]
            format_counts[fmt] += 1
            format_files[fmt].append(f)

        buttons = [
            [InlineKeyboardButton(
                f"{fmt} ({count} files)",
                callback_data=f"pickformat|{jobid}|{fmt}",
            )]
            for fmt, count in sorted(format_counts.items())
        ]
        buttons.append(
            [InlineKeyboardButton("✖ Cancel", callback_data=f"cancel|{jobid}")]
        )
        await msg.edit(
            f"Available formats (total {len(files)} files):\nChoose a format:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as exc:
        logger.exception(exc)
        await msg.edit(f"Error: {exc}")


@app.on_callback_query(filters.regex(r"^pickformat\|"))
@authorized
async def pickformat(client, cq):
    _, jobid, format_ = cq.data.split("|", 2)
    await cq.answer()

    job = JOBS.get(jobid)
    if not job:
        await cq.message.edit("Job not found.", reply_markup=None)
        return

    remotes = rclone_list_remotes(RCLONE_CONFIG_PATH)
    if not remotes:
        await cq.message.edit(
            "No remotes in rclone.conf. Upload one with /set_rclone_conf.",
            reply_markup=None,
        )
        return

    buttons = [
        [InlineKeyboardButton(r, callback_data=f"upload|{jobid}|{format_}|{r}")]
        for r in remotes
    ]
    await cq.message.edit(
        f"Selected format: {format_}\nChoose destination remote:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@app.on_callback_query(filters.regex(r"^upload\|"))
@authorized
async def upload(client, cq):
    _, jobid, format_, remote = cq.data.split("|", 3)
    await cq.answer()

    job = JOBS.get(jobid)
    if not job:
        await cq.message.edit("Job not found.", reply_markup=None)
        return

    ident      = job["identifier"]
    target_dir = TEMP_DIR / ident
    target_dir.mkdir(parents=True, exist_ok=True)
    remote_path = f"{remote}:Archive/{ident}"

    await cq.message.edit(
        f"Downloading all {format_} files for `{ident}` …\n(Please wait)",
        reply_markup=None,
    )
    m = cq.message

    try:
        total_files     = sum(1 for f in job["files"] if f["format"] == format_)
        downloaded_count = 0

        for file_info in job["files"]:
            if file_info["format"] != format_:
                continue

            filename = file_info["name"]

            # ── Path-traversal guard ──────────────────────────────────────────
            try:
                local_path = safe_child_path(target_dir, filename)
            except ValueError as exc:
                logger.error("Path traversal blocked: %s", exc)
                continue

            safe_filename = quote(filename, safe="/")
            url = f"https://archive.org/download/{ident}/{safe_filename}"

            success = False
            for attempt in range(3):
                try:
                    await asyncio.to_thread(stream_download, url, local_path)
                    await asyncio.to_thread(
                        rclone_copy, str(local_path), remote_path,
                        RCLONE_CONFIG_PATH, [],
                    )
                    downloaded_count += 1
                    logger.info(
                        "Uploaded %s (%d/%d)", filename, downloaded_count, total_files
                    )
                    success = True
                    break
                except Exception as exc:
                    logger.error("Attempt %d failed for %s: %s", attempt + 1, filename, exc)
                    local_path.unlink(missing_ok=True)
                    if attempt < 2:
                        await asyncio.sleep(5)

            if not success:
                logger.error("Gave up on %s after 3 attempts", filename)

        await m.edit(
            f"Done! {downloaded_count}/{total_files} {format_} files → "
            f"`{remote}:Archive/{ident}`"
        )
    except Exception as exc:
        logger.exception(exc)
        await m.edit(f"Error: {exc}")
    finally:
        shutil.rmtree(target_dir, ignore_errors=True)
        JOBS.pop(jobid, None)


@app.on_callback_query(filters.regex(r"^cancel\|"))
@authorized
async def cancel(client, cq):
    _, jobid = cq.data.split("|", 1)
    await cq.answer("Operation cancelled.")
    JOBS.pop(jobid, None)
    await cq.message.edit("Operation cancelled.", reply_markup=None)


@app.on_message(filters.command("set_rclone_conf"))
@authorized
async def set_rclone_conf(client, message):
    await message.reply_text(
        "Please reply with your rclone.conf file.\n"
        "⚠️ The file contains sensitive credentials — "
        "make sure you trust this chat."
    )


@app.on_message(filters.document)
@authorized
async def on_document(client, message):
    doc = message.document
    if not doc:
        return

    if "rclone.conf" in doc.file_name.lower():
        target = pathlib.Path(RCLONE_CONFIG_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)

        await message.download(file_name=str(target))

        # Set restrictive permissions — only owner can read/write
        target.chmod(0o600)

        await message.reply_text(f"Saved rclone config to `{target}` (mode 600).")
        await asyncio.sleep(2)
        try:
            await message.delete()
        except Exception as exc:
            logger.warning("Could not delete rclone.conf message: %s", exc)
    else:
        await message.reply_text("Upload must be named rclone.conf")


if __name__ == "__main__":
    if not ALLOWED_USER_IDS:
        logger.warning(
            "ALLOWED_USER_IDS is not set — the bot is open to ALL Telegram users! "
            "Set this env var in production."
        )
    app.run()
