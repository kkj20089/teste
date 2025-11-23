import os
import logging
import asyncio
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo
from telethon.errors import ChatForwardsRestrictedError
from aiohttp import web

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
API_ID = int(os.getenv("API_ID", "27810480"))
API_HASH = os.getenv("API_HASH", "845548fc2f5ec8392b40902a75d025f1")
BOT_TOKEN = os.getenv("BOT_TOKEN", "7602262116:AAH9-_RGXcL8eHpeQmBLN7tvKW5Nl74AKSY")
PRIVATE_CHANNEL_ID = int(os.getenv("PRIVATE_CHANNEL_ID", "-1003318704419"))
PORT = int(os.getenv("PORT", 8080))

# ==========================================
# 📝 LOGGING
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
routes = web.RouteTableDef()

# ==========================================
# 🌐 HIGH-CAPACITY STREAMER (3GB+)
# ==========================================

@routes.get('/')
async def home(request):
    return web.Response(text="🟢 Bot Online: High Capacity Mode")

@routes.get('/stream/{channel_id}/{msg_id}')
async def stream_handler(request):
    try:
        channel_id = int(request.match_info['channel_id'])
        msg_id = int(request.match_info['msg_id'])
        full_channel_id = int(f"-100{channel_id}") if not str(channel_id).startswith("-100") else channel_id
        
        message = await client.get_messages(full_channel_id, ids=msg_id)
        
        if not message or not message.media or not hasattr(message.media, 'document'):
            return web.Response(status=404, text="File gone.")

        document = message.media.document
        
        # Smart Filename Detection
        file_name = "video.mp4"
        for attr in document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                file_name = attr.file_name
        
        # Headers optimized for Large Files (Resume Support)
        headers = {
            'Content-Type': document.mime_type,
            'Content-Disposition': f'attachment; filename="{file_name}"',
            'Content-Length': str(document.size),
            'Accept-Ranges': 'bytes', # Critical for pausing/resuming 3GB files
        }

        response = web.StreamResponse(status=200, reason='OK', headers=headers)
        await response.prepare(request)

        # ⚡ 2MB Chunks = Better stability for 3GB files
        # This keeps the connection alive longer
        async for chunk in client.iter_download(document, chunk_size=2 * 1024 * 1024):
            await response.write(chunk)

        await response.write_eof()
        return response

    except Exception as e:
        logger.error(f"Stream Error: {e}")
        # If client disconnects, we just stop. No need to crash.
        return web.Response(status=500, text="Stream Interrupted")

# ==========================================
# 🤖 BOT LOGIC
# ==========================================

@client.on(events.NewMessage(pattern=r'https://t\.me/(.+)'))
async def link_handler(event):
    try:
        url = event.text.strip()
        parts = url.split('/')
        channel_username = parts[-2]
        msg_id = int(parts[-1])
        
        msg = await event.reply("⚡ Analyzing 3GB+ capability...")
        
        original_msg = await client.get_messages(channel_username, ids=msg_id)
        
        if not original_msg or not original_msg.media:
            await msg.edit("❌ No file.")
            return

        # LOGIC: Check for Restriction
        target_msg = original_msg
        backup_status = ""
        
        try:
            # Try instant forward (Only works if NOT restricted)
            forwarded = await client.forward_messages(PRIVATE_CHANNEL_ID, original_msg)
            target_msg = forwarded
            chat_id_clean = str(PRIVATE_CHANNEL_ID).replace("-100", "")
            msg_id_clean = forwarded.id
            backup_status = "✅ **Backup:** Saved to Storage"
        except ChatForwardsRestrictedError:
            # RESTRICTED CONTENT LOGIC
            chat_id_clean = str(original_msg.chat_id).replace("-100", "")
            msg_id_clean = original_msg.id
            backup_status = "⚠️ **Backup:** Skipped (Restricted Content)"
        except Exception:
            chat_id_clean = str(original_msg.chat_id).replace("-100", "")
            msg_id_clean = original_msg.id

        # Generate Link
        base_url = os.environ.get('RENDER_EXTERNAL_URL', "http://localhost:8080")
        stream_link = f"{base_url}/stream/{chat_id_clean}/{msg_id_clean}"
        
        # File Info
        doc = target_msg.media.document
        size_gb = doc.size / (1024 * 1024 * 1024)
        
        duration = "Unknown"
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                # Convert seconds to h:m:s
                m, s = divmod(attr.duration, 60)
                h, m = divmod(m, 60)
                duration = f"{h}h {m}m {s}s"

        text = f"""
🎥 **Media Ready**

📏 **Size:** `{size_gb:.2f} GB`
⏱️ **Duration:** `{duration}`
{backup_status}

🔗 **Stream/Download Link:**
{stream_link}
"""
        await msg.edit(text)

    except Exception as e:
        await event.reply(f"❌ Error: {str(e)}")

async def main():
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🚀 High-Capacity Bot Running on Port {PORT}")
    await client.run_until_disconnected()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
