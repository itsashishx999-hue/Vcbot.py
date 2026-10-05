
# ===== config.py =====

import os
from pathlib import Path

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_IDS = {int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()}
API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "").strip()
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
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

# ===== storage.py =====

import json, asyncio
from .config import REGISTRY, LOCK_FILE

_lock = asyncio.Lock()

def _read(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default

async def accounts():
    async with _lock:
        return _read(REGISTRY, [])

async def save_accounts(items):
    async with _lock:
        REGISTRY.write_text(json.dumps(items, indent=2))

async def locks():
    async with _lock:
        return _read(LOCK_FILE, {})

async def set_lock(owner_id, chat_id):
    async with _lock:
        data = _read(LOCK_FILE, {})
        data[str(owner_id)] = int(chat_id)
        LOCK_FILE.write_text(json.dumps(data, indent=2))

async def get_lock(owner_id):
    async with _lock:
        return _read(LOCK_FILE, {}).get(str(owner_id))

# ===== audio.py =====

import asyncio, uuid
from pathlib import Path


async def ffmpeg_effect(src: str, preset: str = "beast") -> str:
    preset = preset.lower()
    if preset == "ghost":
        filt = "highpass=f=70,lowpass=f=6500,asetrate=44100*0.78,aresample=44100,atempo=1.282051,aecho=0.8:0.55:90|180:0.28|0.14,acompressor=threshold=-28dB:ratio=5:attack=5:release=100:makeup=2,volume=0.42"
    elif preset == "hybrid":
        filt = EFFECTS["beast"]
    else:
        filt = EFFECTS.get(preset, EFFECTS["beast"])
    out = TMP_DIR / f"{uuid.uuid4().hex}.ogg"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg","-y","-i",src,"-af",filt,"-c:a","libopus","-b:a","96k","-ar","48000","-ac","2",str(out),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-2000:])
    return str(out)

async def cleanup(*paths):
    for p in paths:
        try: Path(p).unlink(missing_ok=True)
        except Exception: pass

# ===== tts.py =====

import edge_tts, uuid
from .config import TMP_DIR

async def make_tts(text: str, voice="hi-IN-MadhurNeural"):
    out = TMP_DIR / f"tts_{uuid.uuid4().hex}.mp3"
    await edge_tts.Communicate(text, voice).save(str(out))
    return str(out)

# ===== vc.py =====

import asyncio
from pathlib import Path
from pyrogram import Client
from pytgcalls import PyTgCalls
try:
    from pytgcalls.types import MediaStream
except Exception:
    MediaStream = None

class Account:
    def __init__(self, item, api_id, api_hash, session_dir):
        self.item=item
        self.client=Client(item["session"], api_id=api_id, api_hash=api_hash, workdir=str(session_dir))
        self.call=PyTgCalls(self.client)
        self.started=False

    async def start(self):
        await self.client.start()
        try: await self.call.start()
        except Exception: pass
        self.started=True

    async def play(self, chat_id, audio_path):
        if MediaStream is None:
            raise RuntimeError("Installed PyTgCalls version does not expose MediaStream.")
        # Current PyTgCalls releases accept MediaStream for audio playback.
        await self.call.play(chat_id, MediaStream(audio_path))

    async def stop(self, chat_id):
        try:
            await self.call.leave_call(chat_id)
        except Exception:
            pass

    async def shutdown(self):
        try: await self.call.stop()
        except Exception: pass
        try: await self.client.stop()
        except Exception: pass

class AccountManager:
    def __init__(self, api_id, api_hash, session_dir):
        self.api_id=api_id; self.api_hash=api_hash; self.session_dir=session_dir
        self.accounts={}

    async def load(self, items):
        for item in items:
            if item.get("session") in self.accounts: continue
            a=Account(item,self.api_id,self.api_hash,self.session_dir)
            try:
                await a.start()
                self.accounts[item["id"]]=a
            except Exception as e:
                print("Account start failed:", item.get("id"), e)

    async def broadcast_play(self, chat_id, path):
        results=[]
        async def one(a):
            try:
                await a.play(chat_id,path); return True, None
            except Exception as e: return False, str(e)
        pairs=await asyncio.gather(*(one(a) for a in self.accounts.values()), return_exceptions=False)
        for acc, res in zip(self.accounts.values(), pairs):
            results.append((acc.item.get("id"), *res))
        return results

    async def broadcast_stop(self, chat_id):
        await asyncio.gather(*(a.stop(chat_id) for a in self.accounts.values()), return_exceptions=True)

    async def shutdown(self):
        await asyncio.gather(*(a.shutdown() for a in self.accounts.values()), return_exceptions=True)

# ===== login.py =====

import asyncio, uuid
from pyrogram import Client


class LoginFlow:
    def __init__(self):
        self.pending = {}

    async def begin(self, user_id, phone):
        key=str(user_id)
        client=Client(f"login_{user_id}_{uuid.uuid4().hex[:8]}", api_id=API_ID, api_hash=API_HASH, workdir=str(SESSION_DIR), in_memory=False)
        await client.connect()
        sent=await client.send_code(phone)
        self.pending[key]={"client":client,"phone":phone,"hash":sent.phone_code_hash}
        return key

    async def verify(self, user_id, code, password=None):
        p=self.pending[str(user_id)]
        client=p["client"]
        try:
            await client.sign_in(p["phone"], p["hash"], code.replace(" ",""))
        except Exception as e:
            from pyrogram.errors import SessionPasswordNeeded
            if isinstance(e, SessionPasswordNeeded):
                if not password:
                    return "2FA_REQUIRED"
                await client.check_password(password)
            else:
                raise
        me=await client.get_me()
        # The Client session file is already persisted in SESSION_DIR.
        await client.disconnect()
        self.pending.pop(str(user_id), None)
        return me

    async def cancel(self, user_id):
        p=self.pending.pop(str(user_id), None)
        if p:
            try: await p["client"].disconnect()
            except Exception: pass

# ===== main.py =====

import asyncio, json, os, re, uuid
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest
from pyrogram import Client







login_flow=LoginFlow()
manager=AccountManager(API_ID,API_HASH,SESSION_DIR)
owner_pending={}
play_lock=asyncio.Lock()

def owner(uid): return uid in OWNER_IDS

async def deny(update):
    if update.effective_user and owner(update.effective_user.id): return False
    await update.effective_message.reply_text("⛔ Owner only.")
    return True

async def start(update, context):
    if await deny(update): return
    await update.message.reply_text("👻 Ghost Beast VC Bot online. Use /help")

async def help_cmd(update, context):
    if await deny(update): return
    await update.message.reply_text(
        "🎙 Voice: send a voice/audio/document then use /beast, /deep, /demon, /power, /radio, /clean, /ghost, /hybrid, /shadow\n"
        "🗣 TTS: /tts your text\n"
        "👤 Accounts: /add, /accounts, /remove ID\n"
        "🎧 VC: .auto <chat_id>, .play [preset] as a reply, .stop\n"
        "All controls are owner-only."
    )

async def add_cmd(update, context):
    if await deny(update): return
    owner_pending[update.effective_user.id]={"state":"phone"}
    await update.message.reply_text("📱 Send the Telegram phone number with country code.")

async def accounts_cmd(update, context):
    if await deny(update): return
    items=await accounts()
    if not items:
        await update.message.reply_text("No accounts added.")
        return
    await update.message.reply_text("\n".join(f"• {x['id']} — {x.get('name','')} — {x.get('phone_mask','')}" for x in items))

async def remove_cmd(update, context):
    if await deny(update): return
    if not context.args: return await update.message.reply_text("Usage: /remove ACCOUNT_ID")
    aid=context.args[0]
    items=await accounts()
    kept=[x for x in items if x["id"]!=aid]
    if len(kept)==len(items): return await update.message.reply_text("Account ID not found.")
    await save_accounts(kept)
    await update.message.reply_text("✅ Removed from registry. Delete its session file from /data/sessions if you want a full logout.")

async def status_cmd(update, context):
    if await deny(update): return
    items=await accounts()
    await update.message.reply_text(f"🤖 Online\n👤 Accounts: {len(items)}\n🎧 Active loaded: {len(manager.accounts)}")

async def tts_cmd(update, context):
    if await deny(update): return
    text=" ".join(context.args).strip()
    if not text: return await update.message.reply_text("Usage: /tts text")
    p=await make_tts(text)
    try:
        await update.message.reply_audio(audio=p, caption="🗣️ TTS")
    finally: await cleanup(p)

async def effect_cmd(update, context):
    if await deny(update): return
    preset=context.args[0].lower() if context.args else update.message.text.lstrip("/").split()[0]
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to a voice/audio/document with the effect command.")
    m=update.message.reply_to_message
    src=await m.download_to_drive(custom_path=str(TMP_DIR/f"in_{uuid.uuid4().hex}"))
    out=await ffmpeg_effect(src,preset)
    try: await update.message.reply_audio(audio=out, caption=f"🎙 {preset.upper()}")
    finally: await cleanup(src,out)

async def text_flow(update, context):
    if await deny(update): return
    uid=update.effective_user.id
    if uid in owner_pending:
        st=owner_pending[uid]
        if st["state"]=="phone":
            try:
                await login_flow.begin(uid, update.message.text.strip())
                st["state"]="otp"
                return await update.message.reply_text("🔐 Send OTP. Spaces are allowed, e.g. 1 2 3 4 5")
            except Exception as e:
                owner_pending.pop(uid,None)
                return await update.message.reply_text(f"❌ Login start failed: {e}")
        if st["state"]=="otp":
            try:
                res=await login_flow.verify(uid, update.message.text, None)
                if res=="2FA_REQUIRED":
                    st["state"]="2fa"
                    return await update.message.reply_text("🔑 2FA is enabled. Send your 2FA password.")
                await finish_account(update,res)
                owner_pending.pop(uid,None)
            except Exception as e:
                await update.message.reply_text(f"❌ OTP failed: {e}")
        elif st["state"]=="2fa":
            try:
                res=await login_flow.verify(uid, "", update.message.text)
                await finish_account(update,res)
                owner_pending.pop(uid,None)
            except Exception as e:
                await update.message.reply_text(f"❌ 2FA failed: {e}")
        return

async def finish_account(update, me):
    aid=uuid.uuid4().hex[:8]
    # Login session name is derived from the temporary login client name; find latest session file.
    session_files=sorted(SESSION_DIR.glob("login_*"), key=lambda p:p.stat().st_mtime, reverse=True)
    session_name=session_files[0].stem if session_files else None
    if not session_name: raise RuntimeError("Session file not found.")
    item={"id":aid,"session":session_name,"name":(me.first_name or "")+" "+(me.last_name or "") ,"phone_mask":"••••"+(me.phone[-4:] if me.phone else "")}
    items=await accounts(); items.append(item); await save_accounts(items)
    await manager.load([item])
    await update.message.reply_text(f"✅ Account added: {aid}\n👤 {item['name']}\n📱 {item['phone_mask']}")

async def auto_cmd(update, context):
    if await deny(update): return
    if not context.args: return await update.message.reply_text("Usage: .auto -1001234567890")
    try:
        chat_id=int(context.args[0])
        await set_lock(update.effective_user.id,chat_id)
        await update.message.reply_text(f"🔒 Auto VC locked: {chat_id}")
    except: await update.message.reply_text("Invalid chat ID.")

async def play_cmd(update, context):
    if await deny(update): return
    if not update.message.reply_to_message: return await update.message.reply_text("Reply to a recording with .play")
    chat_id=await get_lock(update.effective_user.id)
    if not chat_id: return await update.message.reply_text("No locked chat. Use .auto <chat_id> first.")
    preset=context.args[0].lower() if context.args else "beast"
    m=update.message.reply_to_message
    src=await m.download_to_drive(custom_path=str(TMP_DIR/f"play_{uuid.uuid4().hex}"))
    out=await ffmpeg_effect(src,preset)
    try:
        results=await manager.broadcast_play(chat_id,out)
        ok=sum(1 for _,success,_ in results if success)
        await update.message.reply_text(f"▶️ Playing {preset.upper()}\nAccounts started: {ok}/{len(results)}")
    finally:
        await cleanup(src,out)

async def stop_cmd(update, context):
    if await deny(update): return
    chat_id=await get_lock(update.effective_user.id)
    if not chat_id: return await update.message.reply_text("No locked chat.")
    await manager.broadcast_stop(chat_id)
    await update.message.reply_text("⏹️ Playback stopped / accounts asked to leave.")

async def plain(update, context):
    # Handle account-login text and ignore other ordinary messages.
    await text_flow(update,context)

async def main():
    if not BOT_TOKEN or not OWNER_IDS or not API_ID or not API_HASH:
        raise RuntimeError("Set BOT_TOKEN, OWNER_IDS, API_ID and API_HASH.")
    items=await accounts()
    await manager.load(items)
    req=HTTPXRequest(connect_timeout=30,read_timeout=180,write_timeout=180,pool_timeout=30)
    app=Application.builder().token(BOT_TOKEN).request(req).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",help_cmd))
    app.add_handler(CommandHandler("add",add_cmd))
    app.add_handler(CommandHandler("accounts",accounts_cmd))
    app.add_handler(CommandHandler("remove",remove_cmd))
    app.add_handler(CommandHandler("status",status_cmd))
    app.add_handler(CommandHandler("tts",tts_cmd))
    for n in EFFECTS:
        app.add_handler(CommandHandler(n,effect_cmd))
    app.add_handler(CommandHandler("ghost",effect_cmd))
    app.add_handler(CommandHandler("hybrid",effect_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,plain))
    app.add_handler(MessageHandler(filters.Regex(r"^\.auto(?:\s+.*)?$"),auto_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.play(?:\s+.*)?$"),play_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.stop$"),stop_cmd))
    try:
        await app.initialize(); await app.start(); await app.updater.start_polling()
        await asyncio.Event().wait()
    finally:
        await manager.shutdown()
        await app.updater.stop(); await app.stop(); await app.shutdown()

if __name__=="__main__":
    asyncio.run(main())
