import os
import asyncio
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid
from pytgcalls import PyTgCalls
from pytgcalls.types.input_stream import AudioPiped

# ================= CONFIGURATIONS =================
API_ID = int(os.environ.get("API_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash_here")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token_here")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
PORT = int(os.environ.get("PORT", 8080))

SESSION_DIR = "sessions"
os.makedirs(SESSION_DIR, exist_ok=True)

# Main Bot Token Client
app = Client(
    "main_boosted_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Global Variables
active_userbots = []
pytgcalls_clients = []
locked_chat = None
current_audio = None
login_states = {}

# ================= RENDER DUMMY WEB SERVER =================
async def handle_root(request):
    return web.Response(text="⚡ OP VC Player Bot is Alive and Running on Render! ⚡")

async def start_web_server():
    server_app = web.Application()
    server_app.router.add_get("/", handle_root)
    runner = web.AppRunner(server_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[+] Web server started on port {PORT}")

# ================= USERBOT LOADER =================
async def load_userbots():
    global active_userbots, pytgcalls_clients
    
    for call in pytgcalls_clients:
        try:
            await call.leave_call()
        except:
            pass
    for client in active_userbots:
        try:
            await client.stop()
        except:
            pass
            
    active_userbots.clear()
    pytgcalls_clients.clear()
    
    if not os.path.exists(SESSION_DIR):
        return

    for filename in os.listdir(SESSION_DIR):
        if filename.endswith(".session"):
            session_name = filename[:-8]
            session_path = os.path.join(SESSION_DIR, session_name)
            try:
                ubot = Client(session_path, api_id=API_ID, api_hash=API_HASH)
                await ubot.start()
                
                call_client = PyTgCalls(ubot)
                await call_client.start()
                
                active_userbots.append(ubot)
                pytgcalls_clients.append(call_client)
                print(f"[+] Loaded Userbot: {session_name}")
            except Exception as e:
                print(f"[-] Failed to load {session_name}: {e}")

# ================= BOT COMMANDS =================
@app.on_message(filters.command("start") & filters.user(OWNER_ID))
async def start_cmd(client: Client, message: Message):
    total = len(active_userbots)
    text = (
        "⚡ **OP BOOSTED VC PLAYER BOT (RENDER)** ⚡\n\n"
        f"🟢 **Active Logged-in IDs:** `{total}`\n\n"
        "👑 **Owner Commands:**\n"
        "• `/add` - Interactive login (Phone -> OTP -> 2FA)\n"
        "• `.recoding <chat_id/link>` - Target chat lock karein (Saved Messages me)\n"
        "• `/play` - Sabhi IDs se VC join karke **Ultra-Boosted Sound** ke sath play karein\n"
        "• `/stop` - Sabhi IDs ko VC se disconnect karein\n"
        "• `/list` - Logged-in accounts ki list dekhein"
    )
    await message.reply(text)

@app.on_message(filters.command("list") & filters.user(OWNER_ID))
async def list_cmd(client: Client, message: Message):
    if not os.path.exists(SESSION_DIR):
        await message.reply("📂 No sessions found.")
        return
    sessions = [f[:-8] for f in os.listdir(SESSION_DIR) if f.endswith(".session")]
    if not sessions:
        await message.reply("📂 No sessions logged in yet.")
        return
    await message.reply(f"📋 **Logged-in Sessions:**\n" + "\n".join([f"• `{s}`" for s in sessions]))

# ================= INTERACTIVE LOGIN (/add) =================
@app.on_message(filters.command("add") & filters.user(OWNER_ID))
async def add_start(client: Client, message: Message):
    user_id = message.from_user.id
    temp_name = f"temp_{user_id}"
    temp_path = os.path.join(SESSION_DIR, temp_name)
    
    if os.path.exists(temp_path + ".session"):
        os.remove(temp_path + ".session")

    temp_client = Client(temp_path, api_id=API_ID, api_hash=API_HASH, in_memory=False)
    
    login_states[user_id] = {
        "step": "waiting_phone",
        "client": temp_client,
        "temp_path": temp_path
    }
    
    await message.reply("📱 **Interactive Login Started**\n\nKripya apna **Phone Number** bhejo (Country code ke sath, jaise: `+919876543210`):")

# ================= OWNER CHAT HANDLER =================
@app.on_message(filters.chat(OWNER_ID) & filters.text)
async def owner_message_handler(client: Client, message: Message):
    global locked_chat, current_audio
    user_id = message.from_user.id
    text = message.text.strip()
    
    if user_id in login_states:
        state = login_states[user_id]
        step = state["step"]
        cl = state["client"]
        
        if step == "waiting_phone":
            phone_number = text
            try:
                await cl.connect()
                sent_code = await cl.send_code(phone_number)
                state["phone"] = phone_number
                state["hash"] = sent_code.phone_code_hash
                state["step"] = "waiting_otp"
                
                await message.reply("📨 **OTP Sent!**\n\nKripya Telegram par aaya hua **OTP code** bhejo (jaise `12345`):")
            except Exception as e:
                await cl.disconnect()
                del login_states[user_id]
                await message.reply(f"❌ **Error sending code:** `{e}`\n`/add` dobara try karo.")
            return

        elif step == "waiting_otp":
            otp_code = text.replace(" ", "")
            phone = state["phone"]
            phone_hash = state["hash"]
            
            try:
                await cl.sign_in(phone, phone_hash, otp_code)
                await cl.disconnect()
                
                session_id = f"user_{len([f for f in os.listdir(SESSION_DIR) if f.endswith('.session')]) + 1}"
                final_path = os.path.join(SESSION_DIR, session_id)
                os.rename(state["temp_path"] + ".session", final_path + ".session")
                
                del login_states[user_id]
                await message.reply(f"✅ **Login Successful!** Session saved as `{session_id}`.\n\nReloading userbots...")
                await load_userbots()
            except SessionPasswordNeeded:
                state["step"] = "waiting_password"
                await message.reply("🔒 **2FA Password Required!**\n\nKripya apne account ka **Two-Step Verification Password** bhejo:")
            except PhoneCodeInvalid:
                await message.reply("❌ **Invalid OTP!** Sahi OTP code dobara bhejo:")
            except Exception as e:
                try:
                    await cl.disconnect()
                except:
                    pass
                del login_states[user_id]
                await message.reply(f"❌ **Login Failed:** `{e}`\n`/add` se dobara shuru karo.")
            return

        elif step == "waiting_password":
            password = text
            cl = state["client"]
            try:
                await cl.check_password(password)
                await cl.disconnect()
                
                session_id = f"user_{len([f for f in os.listdir(SESSION_DIR) if f.endswith('.session')]) + 1}"
                final_path = os.path.join(SESSION_DIR, session_id)
                os.rename(state["temp_path"] + ".session", final_path + ".session")
                
                del login_states[user_id]
                await message.reply(f"✅ **2FA Verified & Login Successful!** Session saved as `{session_id}`.")
                await load_userbots()
            except Exception as e:
                try:
                    await cl.disconnect()
                except:
                    pass
                del login_states[user_id]
                await message.reply(f"❌ **Incorrect Password / Error:** `{e}`\n`/add` se dobara try karo.")
            return

    if text.startswith(".recoding"):
        parts = text.split(" ", 1)
        
        if message.reply_to_message and (message.reply_to_message.audio or message.reply_to_message.voice or message.reply_to_message.document):
            msg = await message.reply("📥 Downloading recording file...")
            current_audio = await message.reply_to_message.download()
            await msg.edit(f"🎯 **Recording Locked Successfully!**\n📂 File: `{current_audio}`\nAb `.recoding <chat_id_or_username>` bhej kar target chat lock karo.")
            return

        if len(parts) < 2:
            await message.reply("⚠️ **Usage:** `.recoding <chat_id>` ya kisi audio/voice file par reply karke `.recoding` likho.")
            return
            
        target_input = parts[1].strip()
        try:
            if target_input.startswith("https://t.me/"):
                target_input = target_input.split("/")[-1]
                
            chat = await client.get_chat(target_input)
            locked_chat = chat.id
            await message.reply(f"🔒 **Target Chat Locked!**\n🏢 Chat: {chat.title}\n🆔 ID: `{locked_chat}`")
        except Exception as e:
            await message.reply(f"❌ **Error resolving chat:** `{e}`")

# ================= PLAY / STOP COMMANDS =================
@app.on_message(filters.command("play") & filters.user(OWNER_ID))
async def play_cmd(client: Client, message: Message):
    global locked_chat, current_audio
    
    if not locked_chat:
        await message.reply("❌ Pehle `.recoding <chat>` karke chat lock karo!")
        return
        
    if not current_audio or not os.path.exists(current_audio):
        await message.reply("❌ Koi recording locked nahi hai! Kisi audio par reply karke `.recoding` karo.")
        return
        
    if not pytgcalls_clients:
        await message.reply("❌ Koi active userbot login nahi hai! `/add` se ID jodo.")
        return

    status_msg = await message.reply(f"🚀 Connecting `{len(pytgcalls_clients)}` IDs to Voice Chat with **Ultra-Boosted Audio Effects**...")

    boosted_audio = AudioPiped(
        current_audio,
        additional_ffmpeg_parameters="-af volume=5.0,bass=g=10:f=110,treble=g=5"
    )

    success = 0
    for call_client in pytgcalls_clients:
        try:
            await call_client.join_group_call(
                locked_chat,
                boosted_audio
            )
            success += 1
        except Exception as e:
            print(f"VC join error: {e}")

    await status_msg.edit(f"🔥 **ULTRA-BOOSTED PLAYBACK STARTED!**\n✅ Active Streams in VC: `{success}/{len(pytgcalls_clients)}` IDs\n🔊 **FX Boost:** 5x Volume + Heavy Bass Active!")

@app.on_message(filters.command("stop") & filters.user(OWNER_ID))
async def stop_cmd(client: Client, message: Message):
    global locked_chat
    if not locked_chat:
        await message.reply("❌ Koi chat locked nahi hai.")
        return

    for call_client in pytgcalls_clients:
        try:
            await call_client.leave_group_call(locked_chat)
        except:
            pass
            
    await message.reply("🛑 **Stopped playback!** All IDs left the Voice Chat.")

# ================= MAIN ENTRY POINT =================
async def main():
    await start_web_server()
    await app.start()
    print("[+] Main Bot Token Started.")
    await load_userbots()
    print("[+] Bot is running smoothly on Render with Web Server & Boosted Audio!")
    await idle()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
