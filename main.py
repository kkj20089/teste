import os
import logging
import asyncio
import gc  # Garbage Collector
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename
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
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

# ==========================================
# 📝 LOGGING SETUP
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
routes = web.RouteTableDef()

@routes.get('/')
async def home_handler(request):
    return web.Response(text="🚀 Bot is Running!")

@routes.get('/favicon.ico')
async def favicon_handler(request):
    return web.Response(status=204)

@routes.get('/stream/{channel_id}/{msg_id}')
async def stream_handler(request):
    try:
        channel_id = int(request.match_info['channel_id'])
        msg_id = int(request.match_info['msg_id'])
        full_channel_id = int(f"-100{channel_id}") if not str(channel_id).startswith("-100") else channel_id
        
        message = await client.get_messages(full_channel_id, ids=msg_id)
        
        if not message or not message.media or not hasattr(message.media, 'document'):
            return web.Response(status=404, text="File not found.")

        document = message.media.document
        
        file_name = "video.mp4"
        if hasattr(document, 'attributes'):
            for attr in document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    file_name = attr.file_name

        headers = {
            'Content-Type': document.mime_type,
            'Content-Disposition': f'attachment; filename="{file_name}"',
            'Content-Length': str(document.size),
            'Accept-Ranges': 'bytes'
        }

        response = web.StreamResponse(status=200, reason='OK', headers=headers)
        await response.prepare(request)

        # ⚡ MEMORY SAFE MODE: 
        # Using 512KB chunks is safer for low-RAM Free Tier
        chunk_size = 512 * 1024 
        
        async for chunk in client.iter_download(document, chunk_size=chunk_size):
            await response.write(chunk)
            # Optional: Clear memory explicitly for very tight RAM
            del chunk 
            
        await response.write_eof()
        gc.collect() # Force clean up memory after download
        return response

    except Exception as e:
        logger.error(f"Stream Error: {e}")
        return web.Response(status=500, text="Server Error")

# ==========================================
# 🤖 TELEGRAM HANDLER
# ==========================================

@client.on(events.NewMessage(pattern=r'https://t\.me/(.+)'))
async def link_handler(event):
    try:
        url = event.text.strip()
        parts = url.split('/')
        channel_username = parts[-2]
        msg_id = int(parts[-1])
        
        msg = await event.reply("⚡ Processing...")
        
        original_msg = await client.get_messages(channel_username, ids=msg_id)
        
        if not original_msg or not original_msg.media:
            await msg.edit("❌ No file found.")
            return

        # LOGIC: If Restricted -> Stream Direct. If Public -> Save to Storage.
        target_msg = original_msg
        stored_text = ""
        
        try:
            forwarded = await client.forward_messages(PRIVATE_CHANNEL_ID, original_msg)
            target_msg = forwarded
            chat_id_clean = str(PRIVATE_CHANNEL_ID).replace("-100", "")
            msg_id_clean = forwarded.id
            stored_text = "\n✅ **Backed up to Storage**"
        except ChatForwardsRestrictedError:
            chat_id_clean = str(original_msg.chat_id).replace("-100", "")
            msg_id_clean = original_msg.id
            stored_text = "\n⚠️ **Restricted Content** (Streaming Direct)"
        except Exception:
            chat_id_clean = str(original_msg.chat_id).replace("-100", "")
            msg_id_clean = original_msg.id

        base_url = os.environ.get('RENDER_EXTERNAL_URL') or "http://localhost:8080"
        stream_link = f"{base_url}/stream/{chat_id_clean}/{msg_id_clean}"
        
        doc = target_msg.media.document
        size_gb = doc.size / (1024 * 1024 * 1024)
        
        display_name = "File"
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                display_name = attr.file_name

        await msg.edit(f"""
🚀 **Download Ready**

📂 `{display_name}`
📦 `{size_gb:.2f} GB`
{stored_text}

🔗 **Link:**
{stream_link}
""")

    except Exception as e:
        logger.error(e)
        await event.reply(f"❌ Error: {str(e)}")

async def start_server():
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌍 Server running on port {PORT}")

async def main():
    await start_server()
    logger.info("🚀 Bot is Online!")
    await client.run_until_disconnected()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
