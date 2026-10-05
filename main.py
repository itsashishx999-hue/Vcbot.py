import asyncio
import json
import os
import uuid
from pathlib import Path

from aiohttp import web
import edge_tts
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded
from pytgcalls import PyTgCalls
try:
    from pytgcalls.types import MediaStream
except Exception:
    MediaStream = None

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_IDS = {
    int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",")
    if x.strip().isdigit()
}
API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "").strip()
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
PORT = int(os.getenv("PORT", "10000"))

SESSION_DIR = DATA_DIR / "sessions"
TMP_DIR = DATA_DIR / "tmp"
REGISTRY = DATA_DIR / "accounts.json"
LOCK_FILE = DATA_DIR / "locks.json"

for p in (DATA_DIR, SESSION_DIR, TMP_DIR):
    p.mkdir(parents=True, exist_ok=True)

EFFECTS = {
    "beast": "highpass=f=55,lowpass=f=11500,bass=g=12:f=82,treble=g=-1,acompressor=threshold=-20dB:ratio=4:attack=4:release=70:makeup=4,volume=1.9",
    "deep": "highpass=f=45,lowpass=f=10500,bass=g=15:f=70,treble=g=-3,acompressor=threshold=-21dB:ratio=4:attack=5:release=80:makeup=4,volume=1.8",
    "demon": "highpass=f=50,lowpass=f=7200,bass=g=10:f=80,treble=g=-9,acompressor=threshold=-20dB:ratio=4:attack=5:release=80:makeup=4,volume=1.75",
    "power": "highpass=f=55,lowpass=f=12500,bass=g=8:f=100,treble=g=4,acompressor=threshold=-19dB:ratio=3.5:attack=4:release=70:makeup=4,volume=1.7",
    "radio": "highpass=f=230,lowpass=f=4300,acompressor=threshold=-22dB:ratio=5:attack=4:release=70:makeup=5,volume=1.9",
    "clean": "highpass=f=50,lowpass=f=14500,acompressor=threshold=-19dB:ratio=2.5:attack=5:release=70:makeup=3,volume=1.5",
    "shadow": "highpass=f=65,lowpass=f=8000,bass=g=8:f=75,treble=g=-6,acompressor=threshold=-22dB:ratio=4:attack=5:release=90:makeup=3,volume=1.7",
}

# =========================
# STORAGE
# =========================
_storage_lock = asyncio.Lock()

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

async def accounts():
    async with _storage_lock:
        return _read_json(REGISTRY, [])

async def save_accounts(items):
    async with _storage_lock:
        REGISTRY.write_text(json.dumps(items, indent=2), encoding="utf-8")

async def set_lock(owner_id, chat_id):
    async with _storage_lock:
        data = _read_json(LOCK_FILE, {})
        data[str(owner_id)] = int(chat_id)
        LOCK_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

async def get_lock(owner_id):
    async with _storage_lock:
        return _read_json(LOCK_FILE, {}).get(str(owner_id))

# =========================
# AUDIO
# =========================
async def ffmpeg_effect(src: str, preset: str = "beast") -> str:
    preset = preset.lower()
    if preset == "ghost":
        filt = (
            "highpass=f=70,lowpass=f=6500,"
            "asetrate=44100*0.78,aresample=44100,atempo=1.282051,"
            "aecho=0.8:0.55:90|180:0.28|0.14,"
            "acompressor=threshold=-28dB:ratio=5:attack=5:release=100:makeup=2,volume=0.42"
        )
    elif preset == "hybrid":
        filt = EFFECTS["beast"]
    else:
        filt = EFFECTS.get(preset, EFFECTS["beast"])

    out = TMP_DIR / f"effect_{uuid.uuid4().hex}.ogg"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", src,
        "-af", filt,
        "-c:a", "libopus", "-b:a", "96k", "-ar", "48000", "-ac", "2",
        str(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-2500:])
    return str(out)

async def cleanup(*paths):
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass

async def make_tts(text: str, voice="hi-IN-MadhurNeural"):
    out = TMP_DIR / f"tts_{uuid.uuid4().hex}.mp3"
    await edge_tts.Communicate(text, voice).save(str(out))
    return str(out)

# =========================
# TELEGRAM USER ACCOUNTS / VC
# =========================
class Account:
    def __init__(self, item):
        self.item = item
        self.client = Client(
            item["session"],
            api_id=API_ID,
            api_hash=API_HASH,
            workdir=str(SESSION_DIR),
        )
        self.call = PyTgCalls(self.client)

    async def start(self):
        await self.client.start()
        try:
            await self.call.start()
        except Exception as e:
            print("PyTgCalls start warning:", type(e).__name__)

    async def play(self, chat_id, audio_path):
        if MediaStream is None:
            raise RuntimeError("PyTgCalls MediaStream is unavailable in the installed version.")
        await self.call.play(chat_id, MediaStream(audio_path))

    async def stop(self, chat_id):
        try:
            await self.call.leave_call(chat_id)
        except Exception:
            pass

    async def shutdown(self):
        try:
            await self.call.stop()
        except Exception:
            pass
        try:
            await self.client.stop()
        except Exception:
            pass

class AccountManager:
    def __init__(self):
        self.accounts = {}

    async def load(self, items):
        for item in items:
            aid = item.get("id")
            if not aid or aid in self.accounts:
                continue
            account = Account(item)
            try:
                await account.start()
                self.accounts[aid] = account
                print(f"Loaded account {aid}")
            except Exception as e:
                print(f"Account {aid} failed to start: {type(e).__name__}: {e}")

    async def broadcast_play(self, chat_id, path):
        if not self.accounts:
            return []

        async def one(account):
            try:
                await account.play(chat_id, path)
                return True, None
            except Exception as e:
                return False, f"{type(e).__name__}: {e}"

        pairs = await asyncio.gather(
            *(one(a) for a in self.accounts.values()),
            return_exceptions=False,
        )
        return [
            (acc.item.get("id"), success, error)
            for acc, (success, error) in zip(self.accounts.values(), pairs)
        ]

    async def broadcast_stop(self, chat_id):
        await asyncio.gather(
            *(a.stop(chat_id) for a in self.accounts.values()),
            return_exceptions=True,
        )

    async def shutdown(self):
        await asyncio.gather(
            *(a.shutdown() for a in self.accounts.values()),
            return_exceptions=True,
        )

# =========================
# /add LOGIN FLOW
# =========================
class LoginFlow:
    def __init__(self):
        self.pending = {}

    async def begin(self, user_id, phone):
        key = str(user_id)
        session_name = f"login_{user_id}_{uuid.uuid4().hex[:8]}"
        client = Client(
            session_name,
            api_id=API_ID,
            api_hash=API_HASH,
            workdir=str(SESSION_DIR),
        )
        await client.connect()
        sent = await client.send_code(phone)
        self.pending[key] = {
            "client": client,
            "phone": phone,
            "hash": sent.phone_code_hash,
            "session": session_name,
        }

    async def verify(self, user_id, code, password=None):
        p = self.pending[str(user_id)]
        client = p["client"]
        try:
            await client.sign_in(
                p["phone"],
                p["hash"],
                code.replace(" ", "").strip(),
            )
        except SessionPasswordNeeded:
            if not password:
                return "2FA_REQUIRED"
            await client.check_password(password)

        me = await client.get_me()
        session_name = p["session"]
        await client.disconnect()
        self.pending.pop(str(user_id), None)
        return me, session_name

    async def cancel(self, user_id):
        p = self.pending.pop(str(user_id), None)
        if p:
            try:
                await p["client"].disconnect()
            except Exception:
                pass

# =========================
# BOT HANDLERS
# =========================
login_flow = LoginFlow()
manager = AccountManager()
owner_pending = {}


def is_owner(user_id):
    return user_id in OWNER_IDS

async def deny(update: Update):
    if update.effective_user and is_owner(update.effective_user.id):
        return False
    if update.effective_message:
        await update.effective_message.reply_text("⛔ Owner only.")
    return True

async def start_cmd(update, context):
    if await deny(update):
        return
    await update.message.reply_text("👻 Ghost Beast VC Bot online. Use /help")

async def help_cmd(update, context):
    if await deny(update):
        return
    await update.message.reply_text(
        "🎙 Voice effects: reply to a voice/audio/document with "
        "/beast /deep /demon /power /radio /clean /ghost /hybrid /shadow\n"
        "🗣 TTS: /tts your text\n"
        "👤 Accounts: /add /accounts /remove ID /status\n"
        "🎧 VC: .auto CHAT_ID → .play [preset] → .stop\n"
        "All controls are owner-only."
    )

async def add_cmd(update, context):
    if await deny(update):
        return
    uid = update.effective_user.id
    owner_pending[uid] = {"state": "phone"}
    await update.message.reply_text(
        "📱 Send the Telegram phone number with country code.\n"
        "Example: +9198XXXXXXXX"
    )

async def accounts_cmd(update, context):
    if await deny(update):
        return
    items = await accounts()
    if not items:
        await update.message.reply_text("No accounts added.")
        return
    await update.message.reply_text(
        "\n".join(
            f"• {x['id']} — {x.get('name','').strip()} — {x.get('phone_mask','')}"
            for x in items
        )
    )

async def remove_cmd(update, context):
    if await deny(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove ACCOUNT_ID")
        return
    aid = context.args[0]
    items = await accounts()
    kept = [x for x in items if x.get("id") != aid]
    if len(kept) == len(items):
        await update.message.reply_text("Account ID not found.")
        return
    account = manager.accounts.pop(aid, None)
    if account:
        await account.shutdown()
    await save_accounts(kept)
    await update.message.reply_text(
        "✅ Account removed from registry. Its session file remains in /data/sessions; "
        "delete it manually only if you want a full logout."
    )

async def status_cmd(update, context):
    if await deny(update):
        return
    items = await accounts()
    await update.message.reply_text(
        f"🤖 Bot: Online\n👤 Saved accounts: {len(items)}\n"
        f"🎧 Loaded accounts: {len(manager.accounts)}"
    )

async def tts_cmd(update, context):
    if await deny(update):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /tts text")
        return
    p = await make_tts(text)
    try:
        await update.message.reply_audio(audio=p, caption="🗣️ TTS")
    finally:
        await cleanup(p)

async def effect_cmd(update, context):
    if await deny(update):
        return
    preset = context.args[0].lower() if context.args else update.message.text.lstrip("/").split()[0]
    if preset not in set(EFFECTS) | {"ghost", "hybrid"}:
        preset = "beast"
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a voice/audio/document with the effect command.")
        return

    m = update.message.reply_to_message
    src = None
    out = None
    try:
        src = await m.download_to_drive(custom_path=str(TMP_DIR / f"in_{uuid.uuid4().hex}"))
        out = await ffmpeg_effect(src, preset)
        await update.message.reply_audio(audio=out, caption=f"🎙 {preset.upper()}")
    except Exception as e:
        await update.message.reply_text(f"❌ Audio processing failed: {type(e).__name__}: {e}")
    finally:
        await cleanup(src, out)

async def auto_cmd(update, context):
    if await deny(update):
        return
    uid = update.effective_user.id
    if context.args:
        raw = context.args[0].strip()
        try:
            chat_id = int(raw)
        except ValueError:
            await update.message.reply_text("❌ Invalid chat ID.")
            return
        await set_lock(uid, chat_id)
        await update.message.reply_text(f"🔒 Auto VC locked: {chat_id}")
        return

    owner_pending[uid] = {"state": "chat_id"}
    await update.message.reply_text(
        "🔒 Send the group/channel chat ID now.\n"
        "Example: -1001234567890"
    )

async def play_cmd(update, context):
    if await deny(update):
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a recording with .play [preset]")
        return
    chat_id = await get_lock(update.effective_user.id)
    if not chat_id:
        await update.message.reply_text("No locked chat. Use .auto CHAT_ID first.")
        return
    if not manager.accounts:
        await update.message.reply_text("No user accounts are loaded. Use /add first.")
        return

    preset = context.args[0].lower() if context.args else "beast"
    if preset not in set(EFFECTS) | {"ghost", "hybrid"}:
        preset = "beast"

    src = None
    out = None
    try:
        src = await update.message.reply_to_message.download_to_drive(
            custom_path=str(TMP_DIR / f"play_{uuid.uuid4().hex}")
        )
        out = await ffmpeg_effect(src, preset)
        results = await manager.broadcast_play(chat_id, out)
        ok = sum(1 for _, success, _ in results if success)
        await update.message.reply_text(
            f"▶️ Playing {preset.upper()}\nAccounts started: {ok}/{len(results)}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Playback failed: {type(e).__name__}: {e}")
    finally:
        await cleanup(src, out)

async def stop_cmd(update, context):
    if await deny(update):
        return
    chat_id = await get_lock(update.effective_user.id)
    if not chat_id:
        await update.message.reply_text("No locked chat.")
        return
    await manager.broadcast_stop(chat_id)
    await update.message.reply_text("⏹️ Playback stopped / accounts asked to leave.")

async def text_flow(update, context):
    if await deny(update):
        return
    uid = update.effective_user.id
    pending = owner_pending.get(uid)
    if not pending:
        return

    state = pending["state"]

    if state == "phone":
        try:
            await login_flow.begin(uid, update.message.text.strip())
            pending["state"] = "otp"
            await update.message.reply_text("🔐 Send OTP. Spaces are allowed, e.g. 1 2 3 4 5")
        except Exception as e:
            owner_pending.pop(uid, None)
            await update.message.reply_text(f"❌ Login start failed: {type(e).__name__}: {e}")
        return

    if state == "otp":
        try:
            result = await login_flow.verify(uid, update.message.text, None)
            if result == "2FA_REQUIRED":
                pending["state"] = "2fa"
                await update.message.reply_text("🔑 2FA is enabled. Send your 2FA password.")
            else:
                me, session_name = result
                await finish_account(update, me, session_name)
                owner_pending.pop(uid, None)
        except Exception as e:
            await update.message.reply_text(f"❌ OTP failed: {type(e).__name__}: {e}")
        return

    if state == "2fa":
        try:
            me, session_name = await login_flow.verify(uid, "", update.message.text)
            await finish_account(update, me, session_name)
            owner_pending.pop(uid, None)
        except Exception as e:
            await update.message.reply_text(f"❌ 2FA failed: {type(e).__name__}: {e}")
        return

    if state == "chat_id":
        try:
            chat_id = int(update.message.text.strip())
            await set_lock(uid, chat_id)
            owner_pending.pop(uid, None)
            await update.message.reply_text(f"🔒 Auto VC locked: {chat_id}")
        except ValueError:
            await update.message.reply_text("❌ Invalid chat ID. Example: -1001234567890")

async def finish_account(update, me, session_name):
    aid = uuid.uuid4().hex[:8]
    item = {
        "id": aid,
        "session": session_name,
        "name": ((me.first_name or "") + " " + (me.last_name or "")).strip(),
        "phone_mask": "••••" + (me.phone[-4:] if me.phone else ""),
    }
    items = await accounts()
    items.append(item)
    await save_accounts(items)
    await manager.load([item])
    await update.message.reply_text(
        f"✅ Account added: {aid}\n👤 {item['name']}\n📱 {item['phone_mask']}"
    )

# =========================
# RENDER WEB HEALTH SERVER
# =========================
async def health(request):
    return web.json_response({"status": "ok", "service": "ghost-beast-vc-bot"})

async def index(request):
    return web.Response(text="Ghost Beast VC Bot is online.")

async def start_http_server():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"HTTP health server listening on 0.0.0.0:{PORT}")
    return runner

# =========================
# MAIN
# =========================
async def main():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not OWNER_IDS:
        missing.append("OWNER_IDS")
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))

    await manager.load(await accounts())
    http_runner = await start_http_server()

    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=180,
        write_timeout=180,
        pool_timeout=30,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("accounts", accounts_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("tts", tts_cmd))

    for name in EFFECTS:
        app.add_handler(CommandHandler(name, effect_cmd))
    app.add_handler(CommandHandler("ghost", effect_cmd))
    app.add_handler(CommandHandler("hybrid", effect_cmd))

    # Regex handlers MUST come before the generic text handler.
    app.add_handler(MessageHandler(filters.Regex(r"^\.auto(?:\s+.*)?$") & ~filters.COMMAND, auto_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.play(?:\s+.*)?$") & ~filters.COMMAND, play_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.stop$") & ~filters.COMMAND, stop_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))

    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        print("Telegram bot polling started.")
        await asyncio.Event().wait()
    finally:
        await manager.shutdown()
        await login_flow.cancel(next(iter(login_flow.pending), "")) if login_flow.pending else None
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await http_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
