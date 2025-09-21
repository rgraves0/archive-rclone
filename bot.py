import os
import logging
import asyncio
import shutil
import requests
from collections import defaultdict
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from archive_scraper import parse_archive_url, fetch_metadata, list_files_from_metadata
from uploader import rclone_copy, rclone_list_remotes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = int(os.environ.get('API_ID'))
API_HASH = os.environ.get('API_HASH')
SESSION_STRING = os.environ.get('SESSION_STRING')  # User session string for User API
TEMP_DIR = os.environ.get('TEMP_DOWNLOAD_DIR', '/downloads')
RCLONE_CONFIG_PATH = os.environ.get('RCLONE_CONFIG_PATH', '/config/rclone.conf')

os.makedirs(TEMP_DIR, exist_ok=True)

# Use User API with session string (supports up to 2GB file size for free users)
app = Client("archive_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

JOBS = {}

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text("Hello! Send /download <archive.org link> to begin.")

@app.on_message(filters.command("download"))
async def download_cmd(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /download https://archive.org/details/<identifier>")
        return
    url = message.command[1]
    ident = parse_archive_url(url)
    if not ident:
        await message.reply_text("Could not parse identifier.")
        return
    msg = await message.reply_text(f"Fetching metadata for: {ident} ...")
    try:
        meta = fetch_metadata(ident)
        files = list_files_from_metadata(meta)
        if not files:
            await msg.edit("No downloadable files found.")
            return
        jobid = f"{message.chat.id}:{message.id}"
        JOBS[jobid] = {'identifier': ident, 'files': files, 'meta': meta}
        
        # Group files by format and count
        format_counts = defaultdict(int)
        format_files = defaultdict(list)
        for f in files:
            fmt = f['format']
            format_counts[fmt] += 1
            format_files[fmt].append(f)
        
        buttons = []
        for fmt, count in sorted(format_counts.items()):
            label = f"{fmt} ({count} files)"
            buttons.append([InlineKeyboardButton(label, callback_data=f"pickformat|{jobid}|{fmt}")])
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{jobid}")])
        
        await msg.edit("Available formats:\nChoose a format to download and upload to the channel:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.exception(e)
        await msg.edit(f"Error: {e}")

@app.on_callback_query(filters.regex(r"^pickformat\|"))
async def pickformat(client, cq):
    _, jobid, format_ = cq.data.split('|', 2)
    await cq.answer()
    job = JOBS.get(jobid)
    if not job:
        await cq.message.edit("Job not found.")
        return
    remotes = rclone_list_remotes(RCLONE_CONFIG_PATH)
    if not remotes:
        await cq.message.edit("No remotes in rclone.conf. Upload one with /set_rclone_conf.")
        return
    buttons = [[InlineKeyboardButton(r, callback_data=f"upload|{jobid}|{format_}|{r}")] for r in remotes]
    await cq.message.edit(f"Selected format: {format_}\nChoose destination:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex(r"^upload\|"))
async def upload(client, cq):
    _, jobid, format_, remote = cq.data.split('|', 3)
    await cq.answer()
    job = JOBS.get(jobid)
    if not job:
        await cq.message.edit("Job not found.")
        return
    ident = job['identifier']
    target_dir = os.path.join(TEMP_DIR, ident)
    os.makedirs(target_dir, exist_ok=True)
    remote_path = f"{remote}:Archive/{ident}"
    m = await cq.message.reply_text(f"Downloading all {format_} files for {ident} ...")
    try:
        downloaded_files = []
        for file_info in job['files']:
            if file_info['format'] == format_:
                filename = file_info['name']
                local_path = os.path.join(target_dir, filename)
                url = f"https://archive.org/download/{ident}/{filename}"
                with requests.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(local_path, 'wb') as fh:
                        for chunk in r.iter_content(1024*1024):
                            if chunk:
                                fh.write(chunk)
                await asyncio.get_event_loop().run_in_executor(None, rclone_copy, local_path, remote_path, RCLONE_CONFIG_PATH, [])
                downloaded_files.append(local_path)
        
        await m.edit(f"Finished! All {format_} files uploaded to {remote}:Archive/{ident}")
        
        # Clean up local storage
        for local_path in downloaded_files:
            try:
                os.remove(local_path)
            except:
                pass
        try:
            shutil.rmtree(target_dir, ignore_errors=True)
        except:
            pass
        
        # Clear job to ready for new tasks
        if jobid in JOBS:
            del JOBS[jobid]
    except Exception as e:
        logger.exception(e)
        await m.edit(f"Error: {e}")

@app.on_callback_query(filters.regex(r"^cancel\|"))
async def cancel(client, cq):
    _, jobid = cq.data.split('|', 1)
    await cq.answer("Operation cancelled.")
    if jobid in JOBS:
        del JOBS[jobid]
    await cq.message.edit("Operation cancelled.")

@app.on_message(filters.command("set_rclone_conf"))
async def set_rclone_conf(client, message):
    await message.reply_text("Please reply with your rclone.conf file.")

@app.on_message(filters.document)
async def on_document(client, message):
    doc = message.document
    if doc and 'rclone.conf' in doc.file_name.lower():
        target = RCLONE_CONFIG_PATH
        target_dir = os.path.dirname(target) or '.'
        os.makedirs(target_dir, exist_ok=True)
        await message.download(file_name=target)
        await message.reply_text(f"Saved rclone config to {target}")
        await asyncio.sleep(2)
        try:
            await message.delete()
        except Exception as e:
            logger.warning(f"Failed to delete message: {e}")
    else:
        await message.reply_text("Upload must be named rclone.conf")

if __name__ == "__main__":
    app.run()
