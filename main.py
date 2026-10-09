import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.errors import PhoneCodeInvalid, PhoneCodeExpired, SessionPasswordNeeded
from pyrogram.types import Message
from pytgcalls import PyTgCalls
from pytgcalls.types.input_stream import AudioPiped

# ===================== CONFIG =====================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
SESSION_DIR = DATA_DIR / "sessions"
RECORDINGS_DIR = DATA_DIR / "recordings"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("ashish-vc-bot")

if not API_ID or not API_HASH or not BOT_TOKEN or not OWNER_ID:
    log.warning("Set API_ID, API_HASH, BOT_TOKEN, and OWNER_ID in Render Environment.")

app = Client(
    "ashish_vc_bot",
    api_id=API_ID or 12345,
    api_hash=API_HASH or "placeholder",
    bot_token=BOT_TOKEN or "123456:placeholder",
    workdir=str(DATA_DIR),
)

userbots = []
calls = []
login_states = {}
locked_chat = None
current_audio = None
web_runner = None


def owner_only(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == OWNER_ID)


async def health(request):
    return web.json_response({
        "status": "ok",
        "service": "ASHISH VC FIGHTING ZONE",
        "userbots_loaded": len(userbots),
    })


async def start_web_server():
    global web_runner
    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    web_runner = web.AppRunner(web_app)
    await web_runner.setup()
    site = web.TCPSite(web_runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Health web server listening on port %s", PORT)


async def close_login_state(user_id):
    state = login_states.pop(user_id, None)
    if state:
        try:
            if state["client"].is_connected:
                await state["client"].disconnect()
        except Exception:
            log.exception("Could not close temporary login client")


async def load_userbots():
    """Load saved Pyrogram user sessions from DATA_DIR/sessions."""
    global userbots, calls

    for call in calls:
        try:
            await call.stop()
        except Exception:
            pass
    for client in userbots:
        try:
            await client.stop()
        except Exception:
            pass
    userbots.clear()
    calls.clear()

    for session_file in sorted(SESSION_DIR.glob("*.session")):
        session_name = session_file.stem
        if session_name.startswith("temp_"):
            continue
        try:
            client = Client(
                session_name,
                api_id=API_ID,
                api_hash=API_HASH,
                workdir=str(SESSION_DIR),
                no_updates=True,
            )
            await client.start()
            call = PyTgCalls(client)
            await call.start()
            userbots.append(client)
            calls.append(call)
            log.info("Loaded user session: %s", session_name)
        except Exception:
            log.exception("Failed to load user session %s", session_name)

    log.info("Loaded %d user account(s)", len(userbots))


def session_name_next():
    used = []
    for path in SESSION_DIR.glob("user_*.session"):
        match = re.fullmatch(r"user_(\d+)", path.stem)
        if match:
            used.append(int(match.group(1)))
    return f"user_{max(used, default=0) + 1}"


async def finish_login(user_id, message):
    state = login_states[user_id]
    temp_client = state["client"]
    try:
        if temp_client.is_connected:
            await temp_client.disconnect()
    except Exception:
        pass

    old_session = SESSION_DIR / f"{state['temp_name']}.session"
    new_name = session_name_next()
    new_session = SESSION_DIR / f"{new_name}.session"
    if not old_session.exists():
        await close_login_state(user_id)
        await message.reply_text("❌ Session file nahi mili. `/add` se dobara try karein.")
        return

    shutil.move(str(old_session), str(new_session))
    # Move any matching session sidecar files if present.
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = SESSION_DIR / f"{state['temp_name']}.session{suffix}"
        if sidecar.exists():
            shutil.move(str(sidecar), str(SESSION_DIR / f"{new_name}.session{suffix}"))

    login_states.pop(user_id, None)
    await message.reply_text(
        f"✅ Login successful. Account saved as `{new_name}`.\n"
        "Sessions contain account access—keep this bot private and never share the files."
    )
    await load_userbots()


@app.on_message(filters.private & filters.command("start"))
async def start_cmd(client: Client, message: Message):
    if not owner_only(message):
        await message.reply_text("⛔ This bot is private.")
        return
    await message.reply_text(
        "⚡ **ASHISH VC FIGHTING ZONE**\n\n"
        f"🟢 Logged-in accounts: `{len(userbots)}`\n\n"
        "**Owner commands**\n"
        "• `/add` — add a Telegram user account\n"
        "• `/cancel` — cancel the current login flow\n"
        "• `/list` — list saved sessions\n"
        "• Reply to an audio/voice file with `.recoding` — select recording\n"
        "• `.recoding <chat_id_or_username>` — select target group\n"
        "• `/play` — start playback in the selected voice chat\n"
        "• `/stop` — stop playback and disconnect accounts\n\n"
        "⚠️ Send OTP/2FA only in this private owner chat. Do not share bot access."
    )


@app.on_message(filters.private & filters.command("cancel"))
async def cancel_cmd(client: Client, message: Message):
    if not owner_only(message):
        return
    await close_login_state(message.from_user.id)
    await message.reply_text("Login flow cancelled.")


@app.on_message(filters.private & filters.command("list"))
async def list_cmd(client: Client, message: Message):
    if not owner_only(message):
        return
    sessions = sorted(p.stem for p in SESSION_DIR.glob("*.session") if not p.stem.startswith("temp_"))
    if not sessions:
        await message.reply_text("📂 No saved user sessions yet. Use `/add`.")
        return
    await message.reply_text("📋 **Saved sessions**\n" + "\n".join(f"• `{s}`" for s in sessions))


@app.on_message(filters.private & filters.command("add"))
async def add_start(client: Client, message: Message):
    if not owner_only(message):
        return
    user_id = message.from_user.id
    await close_login_state(user_id)
    temp_name = f"temp_{user_id}"
    temp_client = Client(
        temp_name,
        api_id=API_ID,
        api_hash=API_HASH,
        workdir=str(SESSION_DIR),
        no_updates=True,
    )
    login_states[user_id] = {
        "step": "waiting_phone",
        "client": temp_client,
        "temp_name": temp_name,
    }
    await message.reply_text(
        "📱 **Login started**\nSend phone number with country code, e.g. `+919876543210`.\n"
        "Use `/cancel` to stop."
    )


@app.on_message(filters.private & filters.text, group=1)
async def owner_text_handler(client: Client, message: Message):
    global locked_chat, current_audio
    if not owner_only(message):
        return
    text = (message.text or "").strip()
    user_id = message.from_user.id

    # Commands should be handled by their command handlers, not treated as OTP/password.
    if text.startswith("/") and not text.startswith("/recoding"):
        return

    if user_id in login_states:
        state = login_states[user_id]
        temp_client = state["client"]
        try:
            if state["step"] == "waiting_phone":
                phone = text.replace(" ", "")
                if not re.fullmatch(r"\+\d{7,15}", phone):
                    await message.reply_text("❌ Phone format invalid. Example: `+919876543210`")
                    return
                await temp_client.connect()
                sent_code = await temp_client.send_code(phone)
                state.update({"step": "waiting_otp", "phone": phone, "phone_code_hash": sent_code.phone_code_hash})
                await message.reply_text("📨 Telegram OTP bhej diya. Code yahin bhejo.")
                return

            if state["step"] == "waiting_otp":
                otp = re.sub(r"\s+", "", text)
                try:
                    await temp_client.sign_in(state["phone"], state["phone_code_hash"], otp)
                except SessionPasswordNeeded:
                    state["step"] = "waiting_password"
                    await message.reply_text("🔐 2-Step Verification password bhejo, ya `/cancel` karo.")
                    return
                except (PhoneCodeInvalid, PhoneCodeExpired):
                    await message.reply_text("❌ OTP invalid/expired. Naya code request karne ke liye `/cancel` then `/add`.")
                    return
                await finish_login(user_id, message)
                return

            if state["step"] == "waiting_password":
                await temp_client.check_password(text)
                await finish_login(user_id, message)
                return
        except Exception as exc:
            log.exception("Login flow failed")
            await close_login_state(user_id)
            await message.reply_text(f"❌ Login error: `{type(exc).__name__}`. `/add` se dobara try karein.")
            return

    if text.lower() == ".recoding" and message.reply_to_message:
        replied = message.reply_to_message
        if not (replied.audio or replied.voice or replied.document):
            await message.reply_text("Reply kisi audio, voice, ya audio-file document par karein.")
            return
        status = await message.reply_text("📥 Downloading recording…")
        try:
            downloaded = await replied.download(file_name=str(RECORDINGS_DIR / ""))
            if not downloaded:
                await status.edit_text("❌ File download nahi ho saki.")
                return
            current_audio = str(Path(downloaded).resolve())
            await status.edit_text(f"🎵 Recording selected:\n`{Path(current_audio).name}`")
        except Exception:
            log.exception("Recording download failed")
            await status.edit_text("❌ Download failed. Check disk space and file type.")
        return

    if text.lower().startswith(".recoding "):
        target = text.split(maxsplit=1)[1].strip()
        try:
            chat = await client.get_chat(target)
            locked_chat = chat.id
            title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)
            await message.reply_text(f"🔒 **Target chat locked**\n🏢 {title}\n🆔 `{locked_chat}`")
        except Exception as exc:
            log.info("Could not resolve target chat: %s", exc)
            await message.reply_text("❌ Chat resolve nahi hua. Numeric chat ID ya public username try karein.")


@app.on_message(filters.private & filters.command("play"))
async def play_cmd(client: Client, message: Message):
    if not owner_only(message):
        return
    if locked_chat is None:
        await message.reply_text("❌ Pehle `.recoding <chat_id_or_username>` bhejein.")
        return
    if not current_audio or not Path(current_audio).is_file():
        await message.reply_text("❌ Recording select nahi hai. Audio par reply karke `.recoding` bhejein.")
        return
    if not calls:
        await message.reply_text("❌ Koi user account active nahi. `/add` karein.")
        return

    status = await message.reply_text(f"🔄 {len(calls)} account(s) se voice chat connect kar raha hoon…")
    success = 0
    errors = []
    for call in calls:
        try:
            stream = AudioPiped(
                current_audio,
                additional_ffmpeg_parameters="-af volume=2.0,bass=g=5:f=110,treble=g=2",
            )
            await call.join_group_call(locked_chat, stream)
            success += 1
        except Exception as exc:
            log.exception("Voice chat join failed")
            errors.append(type(exc).__name__)

    await status.edit_text(
        f"🎧 **Playback request finished**\n"
        f"✅ Connected: `{success}/{len(calls)}`\n"
        + (f"⚠️ Error types: `{', '.join(errors[:5])}`" if errors else "")
        + "\n\nNote: the target must have an active Telegram group voice chat, and accounts need permission to join."
    )


@app.on_message(filters.private & filters.command("stop"))
async def stop_cmd(client: Client, message: Message):
    if not owner_only(message):
        return
    stopped = 0
    for call in calls:
        try:
            await call.leave_call()
            stopped += 1
        except Exception:
            # Some PyTgCalls versions require a chat id; try that API as a fallback.
            try:
                if locked_chat is not None:
                    await call.leave_group_call(locked_chat)
                    stopped += 1
            except Exception:
                log.exception("Could not leave voice chat")
    await message.reply_text(f"🛑 Stop requested. Accounts disconnected: `{stopped}/{len(calls)}`")


async def main():
    if not API_ID or not API_HASH or not BOT_TOKEN or not OWNER_ID:
        raise RuntimeError("Missing environment variables: API_ID, API_HASH, BOT_TOKEN, OWNER_ID")
    await start_web_server()
    await app.start()
    log.info("Bot started")
    await load_userbots()
    await idle()


if __name__ == "__main__":
    asyncio.run(main())
