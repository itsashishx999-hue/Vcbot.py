import asyncio
import json
import os
import re
import uuid
from pathlib import Path

from aiohttp import web
import edge_tts
from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_IDS = {int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()}
API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "").strip()
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
PORT = int(os.getenv("PORT", "10000"))

SESSION_DIR = DATA_DIR / "sessions"
TMP_DIR = DATA_DIR / "tmp"
REGISTRY = DATA_DIR / "accounts.json"
LOCKS = DATA_DIR / "locks.json"
for p in (DATA_DIR, SESSION_DIR, TMP_DIR):
    p.mkdir(parents=True, exist_ok=True)

EFFECTS = {
    # Heavy voice / boost presets
    "beast": "highpass=f=50,lowpass=f=15000,bass=g=14:f=78,treble=g=2,acompressor=threshold=-20dB:ratio=4:attack=4:release=70:makeup=5,loudnorm=I=-14:TP=-1.5:LRA=7",
    "deep": "highpass=f=42,lowpass=f=12000,bass=g=17:f=68,treble=g=-2,acompressor=threshold=-21dB:ratio=4.5:attack=5:release=80:makeup=5,loudnorm=I=-14:TP=-1.5:LRA=7",
    "demon": "highpass=f=45,lowpass=f=8000,bass=g=12:f=75,treble=g=-8,acompressor=threshold=-20dB:ratio=4:attack=5:release=85:makeup=5,loudnorm=I=-15:TP=-1.5:LRA=7",
    "power": "highpass=f=55,lowpass=f=15000,bass=g=9:f=105,treble=g=5,acompressor=threshold=-19dB:ratio=3.5:attack=4:release=70:makeup=5,loudnorm=I=-13.5:TP=-1.5:LRA=7",
    "radio": "highpass=f=230,lowpass=f=4500,acompressor=threshold=-22dB:ratio=5:attack=4:release=70:makeup=6,loudnorm=I=-14:TP=-1.5:LRA=7",
    "clean": "highpass=f=45,lowpass=f=16000,acompressor=threshold=-19dB:ratio=2.5:attack=5:release=70:makeup=3,loudnorm=I=-15:TP=-1.5:LRA=7",
    "shadow": "highpass=f=55,lowpass=f=9000,bass=g=10:f=72,treble=g=-5,acompressor=threshold=-22dB:ratio=4:attack=5:release=90:makeup=4,loudnorm=I=-15:TP=-1.5:LRA=7",
    # Sound-effect presets
    "echo": "highpass=f=50,lowpass=f=14000,aecho=0.8:0.7:180|360:0.35|0.18,acompressor=threshold=-22dB:ratio=3:attack=5:release=80:makeup=3,loudnorm=I=-15:TP=-1.5:LRA=7",
    "reverb": "highpass=f=55,lowpass=f=14500,aecho=0.8:0.65:70|140|210:0.22|0.14|0.08,acompressor=threshold=-22dB:ratio=3:attack=5:release=100:makeup=3,loudnorm=I=-15:TP=-1.5:LRA=7",
    "phone": "highpass=f=280,lowpass=f=3400,acompressor=threshold=-24dB:ratio=5:attack=4:release=70:makeup=6,loudnorm=I=-14:TP=-1.5:LRA=7",
    "megaphone": "highpass=f=160,lowpass=f=6500,acompressor=threshold=-20dB:ratio=5:attack=3:release=60:makeup=6,aecho=0.7:0.45:80:0.18,loudnorm=I=-14:TP=-1.5:LRA=7",
    "robot": "highpass=f=70,lowpass=f=11000,aphaser=in_gain=0.4:out_gain=0.8:delay=3:decay=0.4:speed=0.5:type=t",
    "alien": "highpass=f=60,lowpass=f=11000,asetrate=44100*0.86,aresample=44100,atempo=1.1627907,aecho=0.8:0.5:120|240:0.22|0.10,acompressor=threshold=-24dB:ratio=4:attack=5:release=90:makeup=3,loudnorm=I=-15:TP=-1.5:LRA=7",
    "ghost": "highpass=f=55,lowpass=f=9000,asetrate=44100*0.82,aresample=44100,atempo=1.2195122,aecho=0.85:0.65:120|240:0.30|0.15,acompressor=threshold=-26dB:ratio=5:attack=5:release=100:makeup=3,loudnorm=I=-16:TP=-1.5:LRA=7",
}

BOOSTS = {
    "ultra": "highpass=f=40,lowpass=f=16000,bass=g=12:f=75,acompressor=threshold=-22dB:ratio=4:attack=4:release=70:makeup=6,loudnorm=I=-12.5:TP=-1.2:LRA=6",
    "bass": "highpass=f=40,lowpass=f=15000,bass=g=20:f=65,acompressor=threshold=-22dB:ratio=4:attack=4:release=75:makeup=5,loudnorm=I=-13.5:TP=-1.2:LRA=6",
}


store_lock = asyncio.Lock()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


async def read_accounts():
    async with store_lock:
        return read_json(REGISTRY, [])


async def write_accounts(items):
    async with store_lock:
        tmp = REGISTRY.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(REGISTRY)


async def set_lock(owner_id: int, chat_id: int):
    async with store_lock:
        data = read_json(LOCKS, {})
        data[str(owner_id)] = int(chat_id)
        LOCKS.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def get_lock(owner_id: int):
    async with store_lock:
        return read_json(LOCKS, {}).get(str(owner_id))


async def remove_lock(owner_id: int):
    async with store_lock:
        data = read_json(LOCKS, {})
        data.pop(str(owner_id), None)
        LOCKS.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def cleanup(*paths):
    for p in paths:
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


async def ffmpeg_filter(src: str, preset: str) -> str:
    preset = preset.lower().strip()
    if preset == "hybrid":
        # Deep main voice + controlled echo for a cinematic hybrid sound.
        filt = EFFECTS["deep"] + ",aecho=0.75:0.45:95|190:0.12|0.06"
    elif preset in EFFECTS:
        filt = EFFECTS[preset]
    elif preset in BOOSTS:
        filt = BOOSTS[preset]
    else:
        filt = EFFECTS["beast"]

    out = TMP_DIR / f"fx_{uuid.uuid4().hex}.ogg"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", src, "-vn", "-af", filt,
        "-c:a", "libopus", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        str(out), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-2200:])
    return str(out)


async def make_tts(text: str):
    out = TMP_DIR / f"tts_{uuid.uuid4().hex}.mp3"
    await edge_tts.Communicate(text, "hi-IN-MadhurNeural").save(str(out))
    return str(out)


# ============================================================
# USER ACCOUNT + VC
# ============================================================
class UserAccount:
    def __init__(self, item):
        self.item = item
        self.client = Client(
            item["session"], api_id=API_ID, api_hash=API_HASH,
            workdir=str(SESSION_DIR), no_updates=True,
        )
        self.calls = PyTgCalls(self.client)

    async def start(self):
        await self.client.start()
        await self.calls.start()
        me = await self.client.get_me()
        return me

    async def play(self, chat_id: int, audio_path: str):
        # PyTgCalls 3.x accepts a local media path through MediaStream.
        await self.calls.play(
            chat_id,
            MediaStream(audio_path, video_flags=MediaStream.Flags.IGNORE),
        )

    async def stop(self, chat_id: int):
        try:
            await self.calls.leave_call(chat_id)
        except Exception:
            pass

    async def shutdown(self):
        try:
            await self.calls.stop()
        except Exception:
            pass
        try:
            await self.client.stop()
        except Exception:
            pass


class AccountManager:
    def __init__(self):
        self.loaded = {}

    async def load_saved(self):
        items = await read_accounts()
        for item in items:
            await self.add_runtime(item)

    async def add_runtime(self, item):
        aid = item.get("id")
        if not aid or aid in self.loaded:
            return
        account = UserAccount(item)
        try:
            me = await account.start()
            self.loaded[aid] = account
            print(f"[ACCOUNT] loaded {aid} ({getattr(me, 'id', '?')})")
        except Exception as e:
            print(f"[ACCOUNT] failed {aid}: {type(e).__name__}: {e}")
            await account.shutdown()

    async def remove_runtime(self, aid):
        account = self.loaded.pop(aid, None)
        if account:
            await account.shutdown()

    async def play_all(self, chat_id, path):
        results = []
        for aid, account in list(self.loaded.items()):
            try:
                await account.play(chat_id, path)
                results.append((aid, True, ""))
            except Exception as e:
                results.append((aid, False, f"{type(e).__name__}: {e}"))
        return results

    async def stop_all(self, chat_id):
        await asyncio.gather(*(a.stop(chat_id) for a in self.loaded.values()), return_exceptions=True)

    async def shutdown(self):
        await asyncio.gather(*(a.shutdown() for a in self.loaded.values()), return_exceptions=True)
        self.loaded.clear()


manager = AccountManager()


class LoginFlow:
    def __init__(self):
        self.pending = {}
        self.lock = asyncio.Lock()

    async def begin(self, uid: int, phone: str):
        phone = phone.strip().replace(" ", "")
        if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
            raise ValueError("Phone must include country code, e.g. +9198XXXXXXXX")
        await self.cancel(uid)
        session_name = f"account_{uid}_{uuid.uuid4().hex[:8]}"
        client = Client(session_name, api_id=API_ID, api_hash=API_HASH, workdir=str(SESSION_DIR), no_updates=True)
        await client.connect()
        try:
            sent = await client.send_code(phone)
        except Exception:
            await client.disconnect()
            raise
        self.pending[uid] = {"client": client, "phone": phone, "hash": sent.phone_code_hash, "session": session_name}

    async def verify_otp(self, uid: int, code: str):
        p = self.pending[uid]
        code = re.sub(r"\s+", "", code)
        if not code.isdigit():
            raise ValueError("OTP must contain digits only")
        try:
            await p["client"].sign_in(p["phone"], p["hash"], code)
        except SessionPasswordNeeded:
            return "2FA"
        me = await p["client"].get_me()
        return await self.finish(uid, me)

    async def verify_2fa(self, uid: int, password: str):
        p = self.pending[uid]
        await p["client"].check_password(password)
        me = await p["client"].get_me()
        return await self.finish(uid, me)

    async def finish(self, uid: int, me):
        p = self.pending[uid]
        # Some Pyrogram User objects don't expose phone; never assume it exists.
        phone = getattr(me, "phone", None) or p["phone"]
        item = {
            "id": uuid.uuid4().hex[:8],
            "session": p["session"],
            "name": " ".join(x for x in [getattr(me, "first_name", ""), getattr(me, "last_name", "")] if x).strip() or "Telegram account",
            "phone_mask": "••••" + phone[-4:],
            "telegram_id": int(getattr(me, "id", 0) or 0),
        }
        await p["client"].disconnect()  # saves the authenticated session
        self.pending.pop(uid, None)
        return item

    async def cancel(self, uid: int):
        p = self.pending.pop(uid, None)
        if p:
            try:
                await p["client"].disconnect()
            except Exception:
                pass


login = LoginFlow()
flow = {}


def owner(uid):
    return uid in OWNER_IDS


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Account", callback_data="add")],
        [InlineKeyboardButton("👤 Accounts", callback_data="accounts"), InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("🎧 VC Control", callback_data="vc"), InlineKeyboardButton("🎙 Effects", callback_data="effects")],
        [InlineKeyboardButton("🗣 TTS", callback_data="tts")],
    ])


def effect_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🦍 Beast", callback_data="info:beast"), InlineKeyboardButton("🔥 Deep", callback_data="info:deep")],
        [InlineKeyboardButton("💥 Ultra Boost", callback_data="info:ultra"), InlineKeyboardButton("🔊 Bass Boost", callback_data="info:bass")],
        [InlineKeyboardButton("😈 Demon", callback_data="info:demon"), InlineKeyboardButton("⚡ Power", callback_data="info:power")],
        [InlineKeyboardButton("👻 Ghost", callback_data="info:ghost"), InlineKeyboardButton("🌑 Shadow", callback_data="info:shadow")],
        [InlineKeyboardButton("🎬 Hybrid", callback_data="info:hybrid"), InlineKeyboardButton("✨ Clean", callback_data="info:clean")],
        [InlineKeyboardButton("📞 Phone", callback_data="info:phone"), InlineKeyboardButton("📢 Megaphone", callback_data="info:megaphone")],
        [InlineKeyboardButton("🔁 Echo", callback_data="info:echo"), InlineKeyboardButton("🏛 Reverb", callback_data="info:reverb")],
        [InlineKeyboardButton("🤖 Robot", callback_data="info:robot"), InlineKeyboardButton("👽 Alien", callback_data="info:alien")],
        [InlineKeyboardButton("🎙 Radio", callback_data="info:radio"), InlineKeyboardButton("⬅️ Main Menu", callback_data="menu")],
    ])


def vc_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔒 Set VC / Auto", callback_data="auto")],
        [InlineKeyboardButton("▶️ Play Help", callback_data="playhelp"), InlineKeyboardButton("⏹ Stop", callback_data="stop")],
        [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("⬅️ Main", callback_data="menu")],
    ])


async def guard(update):
    uid = update.effective_user.id if update.effective_user else 0
    if not owner(uid):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ Owner only.")
        return False
    return True


async def start(update, context):
    if not await guard(update): return
    await update.message.reply_text(
        "👻 <b>GHOST BEAST VC</b>\n\nProfessional voice changer + TTS + multi-account VC controller.\n\nChoose an option:",
        parse_mode="HTML", reply_markup=menu(),
    )


async def help_cmd(update, context):
    if not await guard(update): return
    await update.message.reply_text(
        "<b>Commands</b>\n\n"
        "/add — secure account login\n/accounts — saved accounts\n/status — runtime status\n"
        "/tts text — Hindi TTS\n\n"
        "Reply to a voice/audio/document and use one of:\n"
        "/beast /deep /demon /power /radio /clean /ghost /shadow /hybrid\n\n"
        "VC: .auto <chat_id> → reply to recording with .play [preset] → .stop",
        parse_mode="HTML",
    )


async def add_cmd(update, context):
    if not await guard(update): return
    uid = update.effective_user.id
    flow[uid] = "phone"
    await update.message.reply_text("📱 <b>Step 1/3</b>\nSend phone with country code.\nExample: <code>+9198XXXXXXXX</code>\n\n/cancel to abort.", parse_mode="HTML")


async def accounts_cmd(update, context):
    if not await guard(update): return
    items = await read_accounts()
    if not items:
        await update.message.reply_text("👤 No saved accounts.", reply_markup=menu()); return
    rows = []
    for x in items:
        state = "🟢" if x["id"] in manager.loaded else "🔴"
        rows.append(f"{state} <code>{x['id']}</code> — {x.get('name','Account')} — {x.get('phone_mask','')}")
    await update.message.reply_text("<b>Accounts</b>\n\n" + "\n".join(rows), parse_mode="HTML", reply_markup=menu())


async def remove_cmd(update, context):
    if not await guard(update): return
    if not context.args:
        await update.message.reply_text("Usage: /remove ACCOUNT_ID"); return
    aid = context.args[0]
    items = await read_accounts()
    new = [x for x in items if x.get("id") != aid]
    if len(new) == len(items):
        await update.message.reply_text("❌ Account ID not found."); return
    await manager.remove_runtime(aid)
    await write_accounts(new)
    await update.message.reply_text("✅ Account removed from bot registry. The session file remains on persistent storage; revoke/logout the Telegram session separately if needed.")


async def status_cmd(update, context):
    if not await guard(update): return
    items = await read_accounts()
    await update.message.reply_text(
        f"📊 <b>Status</b>\n\n🤖 Bot: ONLINE\n👤 Saved: {len(items)}\n🟢 Loaded: {len(manager.loaded)}\n💾 Data: {DATA_DIR}\n🌐 Port: {PORT}",
        parse_mode="HTML", reply_markup=menu(),
    )


async def tts_cmd(update, context):
    if not await guard(update): return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /tts your text"); return
    msg = await update.message.reply_text("⏳ Generating voice…")
    p = None
    try:
        p = await make_tts(text)
        await update.message.reply_audio(audio=p, caption="🗣️ Hindi TTS")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ TTS failed: {type(e).__name__}: {e}")
    finally:
        await cleanup(p)


async def effect_cmd(update, context):
    if not await guard(update): return
    preset = (context.args[0].lower() if context.args else update.message.text.split()[0].lstrip("/").lower())
    if preset not in set(EFFECTS) | set(BOOSTS) | {"hybrid"}:
        preset = "beast"
    reply = update.message.reply_to_message
    if not reply or not (reply.voice or reply.audio or reply.document):
        await update.message.reply_text("🎙️ Reply to a voice/audio/document, then use the effect command.\nExample: reply → /beast")
        return
    src = out = None
    status = await update.message.reply_text(f"⏳ Processing <b>{preset.upper()}</b>…", parse_mode="HTML")
    try:
        src = await reply.download_to_drive(custom_path=str(TMP_DIR / f"in_{uuid.uuid4().hex}"))
        out = await ffmpeg_filter(src, preset)
        await update.message.reply_audio(audio=out, caption=f"🎙 {preset.upper()} • Ghost Beast")
        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ Processing failed: {type(e).__name__}: {e}")
    finally:
        await cleanup(src, out)


async def auto_cmd(update, context):
    if not await guard(update): return
    uid = update.effective_user.id
    if context.args:
        try:
            cid = int(context.args[0]); await set_lock(uid, cid)
            await update.message.reply_text(f"🔒 <b>VC locked</b>\n<code>{cid}</code>", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Invalid chat ID.")
        return
    flow[uid] = "chat_id"
    await update.message.reply_text("🔒 Send the group chat ID.\nExample: <code>-1001234567890</code>\n\n/cancel to abort.", parse_mode="HTML")


async def play_cmd(update, context):
    if not await guard(update): return
    cid = await get_lock(update.effective_user.id)
    if not cid:
        await update.message.reply_text("🔒 No VC locked. Use .auto first."); return
    if not manager.loaded:
        await update.message.reply_text("👤 No loaded accounts. Use /add first."); return
    reply = update.message.reply_to_message
    if not reply or not (reply.voice or reply.audio or reply.document):
        await update.message.reply_text("▶️ Reply to a recording/audio and use .play beast"); return
    preset = context.args[0].lower() if context.args else "beast"
    if preset not in set(EFFECTS) | set(BOOSTS) | {"hybrid"}: preset = "beast"
    src = out = None
    status = await update.message.reply_text("⏳ Preparing VC playback…")
    try:
        src = await reply.download_to_drive(custom_path=str(TMP_DIR / f"play_{uuid.uuid4().hex}"))
        out = await ffmpeg_filter(src, preset)
        results = await manager.play_all(cid, out)
        ok = sum(1 for _, good, _ in results if good)
        bad = [f"{aid}: {err}" for aid, good, err in results if not good]
        text = f"▶️ <b>{preset.upper()}</b>\n🎧 VC: <code>{cid}</code>\n🟢 Started: {ok}/{len(results)}"
        if bad: text += "\n\n⚠️ " + "\n".join(bad[:3])
        await status.edit_text(text, parse_mode="HTML")
    except Exception as e:
        await status.edit_text(f"❌ Playback failed: {type(e).__name__}: {e}")
    finally:
        await cleanup(src, out)


async def stop_cmd(update, context):
    if not await guard(update): return
    cid = await get_lock(update.effective_user.id)
    if not cid:
        await update.message.reply_text("🔒 No VC locked."); return
    await manager.stop_all(cid)
    await update.message.reply_text(f"⏹️ Stopped all loaded accounts in <code>{cid}</code>.", parse_mode="HTML")


async def cancel_cmd(update, context):
    if not await guard(update): return
    uid = update.effective_user.id
    flow.pop(uid, None)
    await login.cancel(uid)
    await update.message.reply_text("✅ Current operation cancelled.", reply_markup=menu())


async def text_flow(update, context):
    if not await guard(update): return
    uid = update.effective_user.id
    state = flow.get(uid)
    if not state:
        return
    text = update.message.text.strip()
    if text.lower() == "/cancel":
        await cancel_cmd(update, context); return

    if state == "phone":
        try:
            await update.message.reply_text("⏳ Connecting to Telegram and sending OTP…")
            await login.begin(uid, text)
            flow[uid] = "otp"
            await update.message.reply_text("🔐 <b>Step 2/3</b>\nSend the OTP. Spaces are allowed.\nExample: <code>1 2 3 4 5</code>", parse_mode="HTML")
        except PhoneNumberInvalid:
            await update.message.reply_text("❌ Telegram rejected that phone number. Send it again with +countrycode.")
        except FloodWait as e:
            await update.message.reply_text(f"⏳ Telegram rate limit. Try again after {e.value} seconds.")
        except Exception as e:
            flow.pop(uid, None); await update.message.reply_text(f"❌ Could not send OTP: {type(e).__name__}: {e}")
        return

    if state == "otp":
        try:
            result = await login.verify_otp(uid, text)
            if result == "2FA":
                flow[uid] = "2fa"
                await update.message.reply_text("🔑 <b>Step 3/3</b>\n2-step verification is enabled. Send your Telegram 2FA password.\n\n/cancel to abort.", parse_mode="HTML")
            else:
                await save_new_account(update, result)
                flow.pop(uid, None)
        except PhoneCodeInvalid:
            await update.message.reply_text("❌ OTP is invalid. Check the newest Telegram code and send it again. The login session is still waiting.")
        except PhoneCodeExpired:
            flow.pop(uid, None); await login.cancel(uid); await update.message.reply_text("⌛ OTP expired. Use /add again to request a new code.")
        except FloodWait as e:
            await update.message.reply_text(f"⏳ Telegram rate limit. Wait {e.value} seconds.")
        except Exception as e:
            await update.message.reply_text(f"❌ OTP step failed: {type(e).__name__}: {e}")
        return

    if state == "2fa":
        try:
            item = await login.verify_2fa(uid, text)
            await save_new_account(update, item)
            flow.pop(uid, None)
        except PasswordHashInvalid:
            await update.message.reply_text("❌ Wrong 2FA password. Try again or /cancel.")
        except Exception as e:
            await update.message.reply_text(f"❌ 2FA failed: {type(e).__name__}: {e}")
        return

    if state == "chat_id":
        try:
            cid = int(text); await set_lock(uid, cid); flow.pop(uid, None)
            await update.message.reply_text(f"🔒 <b>VC locked</b>\n<code>{cid}</code>", parse_mode="HTML", reply_markup=vc_menu())
        except ValueError:
            await update.message.reply_text("❌ Invalid chat ID. Example: -1001234567890")


async def save_new_account(update, item):
    items = await read_accounts()
    # Don't register the same Telegram ID twice.
    if item.get("telegram_id") and any(x.get("telegram_id") == item["telegram_id"] for x in items):
        await update.message.reply_text("ℹ️ This Telegram account is already registered.")
        return
    items.append(item)
    await write_accounts(items)
    # Don't make the user wait for the VC engine startup. The authenticated
    # session is already persisted; load the call engine in the background.
    asyncio.create_task(manager.add_runtime(item))
    await update.message.reply_text(
        f"✅ <b>Login successful</b>\n\n👤 {item['name']}\n📱 {item['phone_mask']}\n🆔 <code>{item['id']}</code>\n\n"
        "⏳ VC engine is loading in background. Use /status in a few seconds.",
        parse_mode="HTML", reply_markup=menu(),
    )


async def callbacks(update: Update, context):
    q = update.callback_query
    if not owner(q.from_user.id):
        await q.answer("Owner only", show_alert=True); return
    await q.answer()
    data = q.data
    if data == "menu":
        await q.edit_message_text("👻 <b>GHOST BEAST VC</b>\nChoose an option:", parse_mode="HTML", reply_markup=menu())
    elif data == "add":
        flow[q.from_user.id] = "phone"
        await q.edit_message_text("📱 <b>Step 1/3</b>\nSend phone with country code.\nExample: <code>+9198XXXXXXXX</code>\n\n/cancel to abort.", parse_mode="HTML")
    elif data == "accounts":
        items = await read_accounts()
        if not items:
            text = "👤 No saved accounts."
        else:
            text = "<b>Accounts</b>\n\n" + "\n".join(f"{'🟢' if x['id'] in manager.loaded else '🔴'} <code>{x['id']}</code> — {x.get('name','Account')}" for x in items)
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=menu())
    elif data == "status":
        items = await read_accounts()
        await q.edit_message_text(f"📊 <b>Status</b>\n\n🤖 Bot: ONLINE\n👤 Saved: {len(items)}\n🟢 Loaded: {len(manager.loaded)}", parse_mode="HTML", reply_markup=menu())
    elif data == "effects":
        await q.edit_message_text("🎙 <b>Effects</b>\nReply to an audio/voice message and use the corresponding command:\n\n/beast  /deep  /demon  /power\n/radio  /clean  /ghost  /shadow  /hybrid", parse_mode="HTML", reply_markup=effect_menu())
    elif data.startswith("info:"):
        p = data.split(":", 1)[1]
        await q.edit_message_text(f"🎙 <b>{p.upper()}</b>\n\nReply to the recording and send <code>/{p}</code>.", parse_mode="HTML", reply_markup=effect_menu())
    elif data == "vc":
        await q.edit_message_text("🎧 <b>VC Control</b>\n\nSet the target group with Auto, then reply to a recording and use .play.", parse_mode="HTML", reply_markup=vc_menu())
    elif data == "auto":
        flow[q.from_user.id] = "chat_id"
        await q.edit_message_text("🔒 Send the target group chat ID.\nExample: <code>-1001234567890</code>", parse_mode="HTML")
    elif data == "playhelp":
        await q.edit_message_text("▶️ <b>Play</b>\n\n1. Use .auto -100...\n2. Reply to a recording\n3. Send <code>.play beast</code>\n\nPresets: beast, deep, ultra, bass, demon, power, ghost, shadow, hybrid, clean, radio, phone, megaphone, echo, reverb, robot, alien.", parse_mode="HTML", reply_markup=vc_menu())
    elif data == "stop":
        cid = await get_lock(q.from_user.id)
        if not cid:
            await q.edit_message_text("🔒 No VC locked.", reply_markup=vc_menu()); return
        await manager.stop_all(cid)
        await q.edit_message_text(f"⏹️ Stopped playback in <code>{cid}</code>.", parse_mode="HTML", reply_markup=vc_menu())
    elif data == "tts":
        await q.edit_message_text("🗣 <b>TTS</b>\nUse: <code>/tts आपका टेक्स्ट यहाँ</code>", parse_mode="HTML", reply_markup=menu())


# ============================================================
# RENDER HEALTH SERVER
# ============================================================
async def health(_):
    return web.json_response({"status": "ok", "service": "ghost-beast-vc-bot"})


async def index(_):
    return web.Response(text="Ghost Beast VC Bot is online.")


async def start_http():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"[WEB] listening on 0.0.0.0:{PORT}")
    return runner


async def main():
    missing = []
    if not BOT_TOKEN: missing.append("BOT_TOKEN")
    if not OWNER_IDS: missing.append("OWNER_IDS")
    if not API_ID: missing.append("API_ID")
    if not API_HASH: missing.append("API_HASH")
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))

    await manager.load_saved()
    runner = await start_http()
    request = HTTPXRequest(connect_timeout=20, read_timeout=120, write_timeout=120, pool_timeout=20)
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("accounts", accounts_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("tts", tts_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    for name in sorted(set(EFFECTS) | set(BOOSTS) | {"hybrid"}):
        app.add_handler(CommandHandler(name, effect_cmd))

    # Dot commands MUST be before generic text flow.
    app.add_handler(MessageHandler(filters.Regex(r"^\.auto(?:\s+.*)?$") & filters.TEXT, auto_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.play(?:\s+.*)?$") & filters.TEXT, play_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.stop$") & filters.TEXT, stop_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print("[BOT] polling started")
    try:
        await asyncio.Event().wait()
    finally:
        await login.cancel(next(iter(login.pending), 0) if login.pending else 0)
        await manager.shutdown()
        await app.updater.stop(); await app.stop(); await app.shutdown(); await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
