import os
import logging
import asyncio
import shutil
import requests
from urllib.parse import quote
from collections import defaultdict
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from archive_scraper import parse_archive_url, fetch_metadata, list_files_from_metadata
from uploader import rclone_copy, rclone_list_remotes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = int(os.environ.get('API_ID'))
API_HASH = os.environ.get('API_HASH')
BOT_TOKEN = os.environ.get('BOT_TOKEN')
TEMP_DIR = os.environ.get('TEMP_DOWNLOAD_DIR', '/downloads')
RCLONE_CONFIG_PATH = os.environ.get('RCLONE_CONFIG_PATH', '/config/rclone.conf')

os.makedirs(TEMP_DIR, exist_ok=True)

app = Client("archive_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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
        
        await msg.edit(f"Available formats:\nChoose a format to download and upload to the channel: (Total files: {len(files)})", reply_markup=InlineKeyboardMarkup(buttons))
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
        total_files = sum(1 for f in job['files'] if f['format'] == format_)
        downloaded_count = 0
        for file_info in job['files']:
            if file_info['format'] == format_:
                filename = file_info['name']
                local_path = os.path.join(target_dir, filename)
                
                # 2025 FIX: URL Encode the filename to handle #, spaces, and other special chars correctly
                safe_filename = quote(filename)
                url = f"https://archive.org/download/{ident}/{safe_filename}"
                
                success = False
                for attempt in range(3):  # Retry 3 times
                    try:
                        # Using requests in a thread to avoid blocking the async loop heavily
                        # Note: For very large files, consider using aiohttp in the future.
                        with requests.get(url, stream=True, timeout=60) as r:
                            r.raise_for_status()
                            with open(local_path, 'wb') as fh:
                                for chunk in r.iter_content(1024*1024):
                                    if chunk:
                                        fh.write(chunk)
                        
                        await asyncio.sleep(1)  # Respect rate limit
                        
                        # 2025 UPDATE: Use asyncio.to_thread for cleaner non-blocking execution
                        await asyncio.to_thread(rclone_copy, local_path, remote_path, RCLONE_CONFIG_PATH, [])
                        
                        downloaded_files.append(local_path)
                        downloaded_count += 1
                        logger.info(f"Downloaded and uploaded: {filename} ({downloaded_count}/{total_files})")
                        success = True
                        break
                    except Exception as e:
                        logger.error(f"Attempt {attempt+1} failed for {filename}: {e}")
                        await asyncio.sleep(5)  # Wait before retry
                if not success:
                    logger.error(f"Failed to process {filename} after 3 attempts")
        
        await m.edit(f"Finished! {downloaded_count}/{total_files} {format_} files uploaded to {remote}:Archive/{ident}")
        
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
        # Clean up on error
        try:
            shutil.rmtree(target_dir, ignore_errors=True)
        except:
            pass

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
