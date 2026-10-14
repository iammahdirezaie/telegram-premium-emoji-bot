import os
import re
import asyncio
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv

from telethon import TelegramClient, events, Button
from telethon.tl.types import (
    MessageEntityCustomEmoji,
    InputStickerSetShortName,
)
from telethon.tl.functions.channels import JoinChannelRequest, GetParticipantRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, GetStickerSetRequest
from telethon.errors import UserAlreadyParticipantError, FloodWaitError

load_dotenv()

API_ID = int(os.getenv("API_ID") or 0)
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PASSWORD_2FA = os.getenv("PASSWORD_2FA", "")
PREMIUM_USER_ID = int(os.getenv("PREMIUM_USER_ID") or 0)
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)

CARD_NUMBER = os.getenv("CARD_NUMBER", "0000-0000-0000-0000")
CARD_OWNER = os.getenv("CARD_OWNER", "نام صاحب حساب")
FALLBACK_CHAR = "⭐"

ICON_VERIFIED = '<tg-emoji emoji-id="5417984163095542602">✅</tg-emoji>'
ICON_ROCKET   = '<tg-emoji emoji-id="5350291836378307462">🚀</tg-emoji>'
ICON_CROWN    = '<tg-emoji emoji-id="5210783786706436474">👑</tg-emoji>'
ICON_CARD     = '<tg-emoji emoji-id="5417969813609795944">💳</tg-emoji>'
ICON_ALERT    = '<tg-emoji emoji-id="5321529789016740670">⚠️</tg-emoji>'

bot_client = TelegramClient("bot_session", API_ID, API_HASH, sequential_updates=True)
user_client = TelegramClient("premium_user_session", API_ID, API_HASH)


def init_db():
    with sqlite3.connect("bot_data.db") as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                expire_date TEXT,
                is_banned INTEGER DEFAULT 0,
                max_channels INTEGER DEFAULT 2,
                invited_by INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_id INTEGER,
                title TEXT,
                username TEXT,
                added_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                user_id INTEGER,
                added_time TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS aliases (
                code TEXT PRIMARY KEY,
                emoji_id TEXT,
                name TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                code TEXT PRIMARY KEY,
                discount_percent INTEGER,
                max_uses INTEGER,
                used_count INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('force_join_channel', '')")
        if ADMIN_ID:
            c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (ADMIN_ID,))

        default_emojis = [
            ("a1", "5350291836378307462", "Pack 1"),
            ("a2", "5417984163095542602", "Pack 1"),
            ("a3", "5210783786706436474", "Pack 1"),
            ("b1", "5350291836378307462", "Pack 2"),
            ("b2", "5417984163095542602", "Pack 2"),
        ]
        c.executemany("INSERT OR IGNORE INTO aliases (code, emoji_id, name) VALUES (?, ?, ?)", default_emojis)
        conn.commit()

init_db()


def get_or_create_user(user_id: int, referrer_id: int = 0):
    with sqlite3.connect("bot_data.db") as conn:
        user = conn.execute("SELECT user_id, expire_date, is_banned, max_channels, invited_by FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not user:
            expire_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("INSERT INTO users (user_id, expire_date, is_banned, max_channels, invited_by) VALUES (?, ?, 0, 2, ?)", (user_id, expire_date, referrer_id))
            conn.commit()
            
            if referrer_id and referrer_id != user_id:
                with sqlite3.connect("bot_data.db") as r_conn:
                    refs_count = r_conn.execute("SELECT COUNT(*) FROM users WHERE invited_by = ?", (referrer_id,)).fetchone()[0]
                
                if refs_count > 0 and refs_count % 10 == 0:
                    add_subscription_days(referrer_id, 5)
                    asyncio.create_task(notify_referrer_bonus(referrer_id, refs_count))
                else:
                    asyncio.create_task(notify_referrer_joined(referrer_id))
            
            return expire_date, 0
        return user[1], user[2]

async def notify_referrer_joined(referrer_id: int):
    try:
        await bot_client.send_message(
            referrer_id,
            "🎉 <b>یک کاربر جدید با لینک دعوت شما وارد ربات شد.</b>\n"
            "با دعوت از هر ۱۰ نفر، ۵ روز اشتراک رایگان دریافت می‌کنید.",
            parse_mode="html"
        )
    except Exception:
        pass

async def notify_referrer_bonus(referrer_id: int, total_refs: int):
    try:
        await bot_client.send_message(
            referrer_id,
            f"🎁 <b>تعداد دعوت‌های شما به {total_refs} نفر رسید!</b>\n"
            "به عنوان هدیه، <b>۵ روز</b> اشتراک رایگان به حساب شما اضافه شد.",
            parse_mode="html"
        )
    except Exception:
        pass

def is_user_banned(user_id: int) -> bool:
    with sqlite3.connect("bot_data.db") as conn:
        res = conn.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return bool(res and res[0] == 1)

def set_ban_status(user_id: int, status: int):
    with sqlite3.connect("bot_data.db") as conn:
        conn.execute("INSERT INTO users (user_id, expire_date, is_banned, max_channels) VALUES (?, datetime('now'), ?, 2) ON CONFLICT(user_id) DO UPDATE SET is_banned = ?", (user_id, status, status))
        conn.commit()

def is_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    with sqlite3.connect("bot_data.db") as conn:
        res = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        return bool(res)

def check_subscription(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    with sqlite3.connect("bot_data.db") as conn:
        user = conn.execute("SELECT expire_date, is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not user or user[1] == 1:
            return False
        try:
            exp = datetime.strptime(user[0], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return False
        
        if exp <= datetime.now():
            new_exp = datetime.now() + timedelta(days=1)
            new_exp_str = new_exp.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE users SET expire_date = ? WHERE user_id = ?", (new_exp_str, user_id))
            conn.commit()
            asyncio.create_task(notify_expired_auto_renew(user_id, new_exp_str))
            return True
        return True

async def notify_expired_auto_renew(user_id: int, new_date: str):
    try:
        await bot_client.send_message(
            user_id,
            f"{ICON_ALERT} <b>اشتراک شما به پایان رسیده بود.</b>\n"
            f"به صورت خودکار ۱ روز مهلت تست به حساب شما اضافه شد. اعتبار جدید: <code>{new_date}</code>",
            parse_mode="html"
        )
    except Exception:
        pass

def add_subscription_days(user_id: int, days: int, extra_channels_add: int = 0):
    with sqlite3.connect("bot_data.db") as conn:
        user = conn.execute("SELECT expire_date, max_channels FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if user and user[0]:
            try:
                curr_exp = datetime.strptime(user[0], "%Y-%m-%d %H:%M:%S")
                new_exp = max(curr_exp, datetime.now()) + timedelta(days=days)
            except Exception:
                new_exp = datetime.now() + timedelta(days=days)
            curr_max = user[1] if user[1] else 2
        else:
            new_exp = datetime.now() + timedelta(days=days)
            curr_max = 2
        
        new_max = curr_max + extra_channels_add
        new_exp_str = new_exp.strftime("%Y-%m-%d %H:%M:%S")
        
        conn.execute(
            "INSERT INTO users (user_id, expire_date, is_banned, max_channels) VALUES (?, ?, 0, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET expire_date = ?, max_channels = ?",
            (user_id, new_exp_str, new_max, new_exp_str, new_max)
        )
        conn.commit()
        return new_exp.strftime("%Y-%m-%d %H:%M")

def get_user_channels(user_id: int):
    with sqlite3.connect("bot_data.db") as conn:
        return conn.execute("SELECT channel_id, title, username FROM channels WHERE user_id = ?", (user_id,)).fetchall()

def get_aliases():
    with sqlite3.connect("bot_data.db") as conn:
        return conn.execute("SELECT code, emoji_id, name FROM aliases").fetchall()

def get_setting(key: str) -> str:
    with sqlite3.connect("bot_data.db") as conn:
        res = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return res[0] if res else ""

def set_setting(key: str, value: str):
    with sqlite3.connect("bot_data.db") as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?", (key, value, value))
        conn.commit()

user_states = {}
user_drafts = {}
bot_username = ""


def main_reply_kb(user_id: int):
    buttons = [
        [Button.text("✍️ ارسال پست با ایموجی", resize=True), Button.text("➕ ثبت و اتصال کانال", resize=True)],
        [Button.text("👤 حساب کاربری من", resize=True), Button.text("⭐️ تمدید اشتراک و تعرفه‌ها", resize=True)],
        [Button.text("📋 لیست بسته‌های ایموجی", resize=True), Button.text("📢 کانال‌های ثبت‌شده من", resize=True)],
        [Button.text("🎁 دعوت از دوستان (هدیه)", resize=True), Button.text("📖 راهنمای گام‌به‌گام", resize=True)],
        [Button.text("📨 درخواست افزودن ایموجی دلخواه", resize=True)]
    ]
    if is_admin(user_id):
        buttons.append([Button.text("👑 پنل مدیریت", resize=True)])
    return buttons

def cancel_reply_kb():
    return [[Button.text("❌ انصراف و بازگشت به منوی اصلی", resize=True)]]

def post_prompt_kb():
    return [
        [Button.text("📋 مشاهده لیست بسته‌های ایموجی", resize=True)],
        [Button.text("❌ انصراف و بازگشت به منوی اصلی", resize=True)]
    ]

def admin_reply_kb():
    return [
        [Button.text("➕ افزودن کل پک ایموجی", resize=True), Button.text("📢 لیست کانال‌های متصل اکانت", resize=True)],
        [Button.text("👤 تنظیم اشتراک کاربر", resize=True), Button.text("📩 ارسال پیام به کاربر", resize=True)],
        [Button.text("📨 پیام همگانی", resize=True), Button.text("🔗 تنظیم جوین اجباری", resize=True)],
        [Button.text("🎟 مدیریت کدهای تخفیف", resize=True), Button.text("➕ افزودن ادمین جدید", resize=True)],
        [Button.text("🚫 مسدود کردن (Ban)", resize=True), Button.text("✅ رفع مسدودیت (Unban)", resize=True)],
        [Button.text("📊 آمار کلی ربات", resize=True), Button.text("❌ خروج از پنل", resize=True)]
    ]


async def check_force_join(event) -> bool:
    channel_username = get_setting("force_join_channel")
    if not channel_username:
        return True
    
    user_id = event.sender_id
    if is_admin(user_id):
        return True

    clean_uname = channel_username.replace("@", "").strip()
    try:
        await user_client(GetParticipantRequest(
            channel=clean_uname,
            participant=user_id
        ))
        return True
    except Exception:
        join_kb = [
            [Button.url("🔗 عضویت در کانال", f"https://t.me/{clean_uname}")],
            [Button.text("🔄 بررسی عضویت", resize=True)]
        ]
        await event.respond(
            f"{ICON_ALERT} برای استفاده از ربات، ابتدا در کانال زیر عضو شوید:\n\n@{clean_uname}\n\nپس از عضویت، روی دکمه زیر بزنید 👇",
            buttons=join_kb,
            parse_mode="html"
        )
        return False


def get_grouped_packs():
    aliases = get_aliases()
    groups = {}
    for code, eid, name in aliases:
        prefix_match = re.match(r"^([a-zA-Z]+)", code)
        p_key = prefix_match.group(1) if prefix_match else "other"
        if p_key not in groups:
            groups[p_key] = {"title": name or p_key, "prefix": p_key, "items": []}
        groups[p_key]["items"].append((code, eid))
    return groups

def render_packs_catalog(page: int = 1, per_page: int = 6):
    groups = get_grouped_packs()
    keys = list(groups.keys())
    total_packs = len(keys)
    if total_packs == 0:
        return f"{ICON_ALERT} در حال حاضر هیچ بسته ایموجی ثبت نشده است.", None

    total_pages = max(1, (total_packs + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    curr_keys = keys[start:start + per_page]

    text = f"<b>📦 بسته‌های ایموجی پرمیوم ({total_packs} بسته)</b>\n\n"
    for k in curr_keys:
        p = groups[k]
        count = len(p["items"])
        icons = "".join([f'<tg-emoji emoji-id="{item[1]}">{FALLBACK_CHAR}</tg-emoji>' for item in p["items"][:4]])
        text += f":{p['prefix']}1: {icons}  <b>{p['title']}</b> ({count} عدد)\n"

    text += "\nبرای مشاهده کدهای هر بسته، روی نام آن کلیک کنید:"

    kb = []
    for i in range(0, len(curr_keys), 2):
        row = [Button.inline(groups[curr_keys[i]]["title"][:20], f"view_pack:{curr_keys[i]}:1".encode())]
        if i + 1 < len(curr_keys):
            row.append(Button.inline(groups[curr_keys[i+1]]["title"][:20], f"view_pack:{curr_keys[i+1]}:1".encode()))
        kb.append(row)

    nav = []
    if page > 1:
        nav.append(Button.inline("⬅️ قبلی", f"page_packs:{page-1}".encode()))
    nav.append(Button.inline(f"صفحه {page} از {total_pages}", b"noop"))
    if page < total_pages:
        nav.append(Button.inline("بعدی ➡️", f"page_packs:{page+1}".encode()))
    if len(nav) > 1:
        kb.append(nav)

    kb.append([Button.inline("❌ بستن کاتالوگ", b"close_catalog")])
    return text, kb

def render_single_pack(prefix: str, page: int = 1, per_page: int = 24):
    groups = get_grouped_packs()
    p_data = groups.get(prefix)
    if not p_data:
        return f"{ICON_ALERT} بسته مورد نظر پیدا نشد.", [[Button.inline("بازگشت", b"page_packs:1")]]

    items = p_data["items"]
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    curr_items = items[start:start + per_page]

    text = f"<b>✨ بسته {p_data['title']}</b> ({total} ایموجی)\n"
    text += f"صفحه {page} از {total_pages}\n"
    text += "<i>کد ایموجی‌ها:</i>\n\n"

    for code, eid in curr_items:
        text += f'<tg-emoji emoji-id="{eid}">{FALLBACK_CHAR}</tg-emoji> <code>:{code}:</code>    '

    kb = []
    nav = []
    if page > 1:
        nav.append(Button.inline("⬅️ قبلی", f"view_pack:{prefix}:{page-1}".encode()))
    if total_pages > 1:
        nav.append(Button.inline(f"صفحه {page} از {total_pages}", b"noop"))
    if page < total_pages:
        nav.append(Button.inline("بعدی ➡️", f"view_pack:{prefix}:{page+1}".encode()))
    if nav:
        kb.append(nav)

    kb.append([Button.inline("🔙 بازگشت به لیست بسته‌ها", b"page_packs:1")])
    return text, kb


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2

def build_entities_for_user(text: str):
    if not text:
        return "", []
    clean_text = text.replace("**", "").replace("*", "")
    aliases = {row[0]: int(row[1]) for row in get_aliases()}
    tokens = re.split(r"(\s+)", clean_text)
    result_text = ""
    entities = []

    for token in tokens:
        clean_token = token.strip(":").strip()
        if clean_token in aliases:
            offset = utf16_len(result_text)
            entities.append(MessageEntityCustomEmoji(
                offset=offset,
                length=utf16_len(FALLBACK_CHAR),
                document_id=aliases[clean_token]
            ))
            result_text += FALLBACK_CHAR
        else:
            result_text += token

    return result_text, entities

def build_html_for_preview(text: str):
    if not text:
        return ""
    processed = text.replace("**", "").replace("*", "")
    aliases = {row[0]: row[1] for row in get_aliases()}
    for code, eid in aliases.items():
        pattern = rf":{re.escape(code)}:"
        replacement = f'<tg-emoji emoji-id="{eid}">{FALLBACK_CHAR}</tg-emoji>'
        processed = re.sub(pattern, replacement, processed)
    return processed

async def verify_admin_rights(target_entity):
    try:
        participant = await user_client(GetParticipantRequest(
            channel=target_entity,
            participant=PREMIUM_USER_ID
        ))
        p_type = type(participant.participant).__name__
        if p_type == "ChannelParticipantCreator":
            return True
        if p_type == "ChannelParticipantAdmin":
            admin_rights = participant.participant.admin_rights
            return getattr(admin_rights, 'post_messages', False)
        return False
    except Exception:
        return False


@bot_client.on(events.NewMessage(func=lambda e: e.is_private))
async def handle_messages(event):
    sender_id = event.sender_id
    raw_content = event.message.message or ""
    text = raw_content.strip()

    if is_user_banned(sender_id) and not is_admin(sender_id):
        await event.respond(f"{ICON_ALERT} حساب کاربری شما مسدود شده است.", parse_mode="html")
        return

    if text in ["❌ انصراف و بازگشت به منوی اصلی", "❌ خروج از پنل"]:
        user_states.pop(sender_id, None)
        user_drafts.pop(sender_id, None)
        await event.respond(f"{ICON_VERIFIED} بازگشت به منوی اصلی:", buttons=main_reply_kb(sender_id), parse_mode="html")
        return

    if text == "🔄 بررسی عضویت":
        if await check_force_join(event):
            await event.respond(f"{ICON_VERIFIED} عضویت شما تایید شد. از منوی زیر انتخاب کنید:", buttons=main_reply_kb(sender_id), parse_mode="html")
        return

    if text.startswith("/start"):
        parts = text.split()
        referrer_id = 0
        if len(parts) > 1 and parts[1].isdigit():
            referrer_id = int(parts[1])

        get_or_create_user(sender_id, referrer_id)
        user_states.pop(sender_id, None)
        user_drafts.pop(sender_id, None)

        if not await check_force_join(event):
            return

        msg = (
            f"{ICON_CROWN} <b>به ربات ارسال پست با ایموجی پرمیوم خوش آمدید 🚀</b>\n\n"
            "با این ربات می‌توانید در کانال‌های خود پست‌های حاوی ایموجی‌های متحرک پرمیوم منتشر کنید.\n"
            "از دکمه‌های زیر استفاده کنید:"
        )
        await event.respond(msg, buttons=main_reply_kb(sender_id), parse_mode="html")
        return

    if not await check_force_join(event):
        return

    if text == "👤 حساب کاربری من":
        with sqlite3.connect("bot_data.db") as conn:
            user = conn.execute("SELECT expire_date, max_channels FROM users WHERE user_id = ?", (sender_id,)).fetchone()
        
        has_sub = check_subscription(sender_id)
        
        if not has_sub:
            vip_title = "کاربر عادی (منقضی شده) 💤"
            sub_status = f"{ICON_ALERT} نیاز به تمدید ❌"
        else:
            vip_title = "اشتراک ویژه 🚀" if sender_id != ADMIN_ID else "مدیر کل 👑"
            sub_status = f"{ICON_VERIFIED} فعال"

        exp_date = user[0] if user else "نامشخص"
        max_ch = user[1] if user else 2
        curr_ch_count = len(get_user_channels(sender_id))

        with sqlite3.connect("bot_data.db") as conn:
            refs_count = conn.execute("SELECT COUNT(*) FROM users WHERE invited_by = ?", (sender_id,)).fetchone()[0]

        acc_msg = (
            f"{ICON_CROWN} <b>اطلاعات حساب کاربری</b>\n\n"
            f"🏅 سطح کاربری: <b>{vip_title}</b>\n"
            f"▫️ شناسه عددی: <code>{sender_id}</code>\n"
            f"▫️ وضعیت اشتراک: {sub_status}\n"
            f"▫️ تاریخ انقضا: <code>{exp_date}</code>\n"
            f"▫️ ظرفیت کانال‌ها: <b>{curr_ch_count} از {max_ch} کانال</b>\n"
            f"👥 زیرمجموعه‌ها: <b>{refs_count} نفر</b>\n"
        )
        await event.respond(acc_msg, buttons=main_reply_kb(sender_id), parse_mode="html")
        return

    if text == "🎁 دعوت از دوستان (هدیه)":
        ref_link = f"https://t.me/{bot_username}?start={sender_id}"
        ref_msg = (
            f"🎁 <b>دریافت اشتراک رایگان با دعوت از دوستان</b>\n\n"
            f"با ارسال لینک اختصاصی زیر برای دوستانتان، به ازای هر <b>۱۰ نفری</b> که وارد ربات شوند، <b>۵ روز اشتراک رایگان</b> به صورت خودکار دریافت خواهید کرد.\n\n"
            f"🔗 لینک اختصاصی شما:\n<code>{ref_link}</code>"
        )
        await event.respond(ref_msg, buttons=main_reply_kb(sender_id), parse_mode="html")
        return

    if text == "📖 راهنمای گام‌به‌گام":
        guide_text = (
            f"{ICON_ROCKET} <b>راهنمای استفاده از ربات:</b>\n\n"
            "۱. <b>تهیه اشتراک:</b>\n"
            "از بخش «⭐️ تمدید اشتراک و تعرفه‌ها» پلن مورد نظر را انتخاب و فیش واریزی را ارسال کنید.\n\n"
            "۲. <b>ثبت کانال:</b>\n"
            "با زدن دکمه «➕ ثبت و اتصال کانال»، آیدی یا لینک کانال خود را ارسال کنید (هر اشتراک ظرفیت ۲ کانال دارد).\n\n"
            "۳. <b>دسترسی ادمین:</b>\n"
            f"اکانت ربات با آیدی عددی <code>{PREMIUM_USER_ID}</code> وارد کانال شما می‌شود. دسترسی ارسال پست (Post Messages) را به آن بدهید.\n\n"
            "۴. <b>ارسال پست:</b>\n"
            "متن یا فایل رسانه را به همراه کدهای ایموجی (مانند <code>:a1:</code>) ارسال کنید تا در کانال منتشر شود."
        )
        await event.respond(guide_text, buttons=main_reply_kb(sender_id), parse_mode="html")
        return

    if text in ["📋 لیست بسته‌های ایموجی", "📋 مشاهده لیست بسته‌های ایموجی"]:
        pack_text, pack_kb = render_packs_catalog(page=1)
        if pack_kb:
            await event.respond(pack_text, buttons=pack_kb, parse_mode="html")
        else:
            await event.respond(pack_text, parse_mode="html")
        return

    if text == "⭐️ تمدید اشتراک و تعرفه‌ها":
        with sqlite3.connect("bot_data.db") as conn:
            user = conn.execute("SELECT expire_date, max_channels FROM users WHERE user_id = ?", (sender_id,)).fetchone()
        exp_date = user[0] if user else "نامشخص"

        info_text = (
            f"{ICON_CARD} <b>تعرفه‌ها و پلن‌های اشتراک</b>\n\n"
            f"📅 انقضای فعلی شما: <code>{exp_date}</code>\n\n"
            f"💰 <b>تعرفه‌ها:</b>\n"
            f"🔸 پکیج ۱۵ روزه (۲ کانال): <b>۵۰,۰۰۰ تومان</b>\n"
            f"🔹 پکیج ۳۰ روزه (۲ کانال): <b>۱۰۰,۰۰۰ تومان</b>\n\n"
            f"💡 <i>نکته: با هر بار تمدید مجدد، علاوه بر زمان، ظرفیت ۲ کانال دیگر نیز به حساب شما اضافه می‌شود.</i>\n\n"
            f"💳 <b>اطلاعات واریز:</b>\n"
            f"شماره کارت: <code>{CARD_NUMBER}</code>\n"
            f"به نام: <b>{CARD_OWNER}</b>\n\n"
            "پس از واریز، روی دکمه زیر بزنید:"
        )
        receipt_kb = [
            [Button.text("📤 ارسال فیش پرداخت", resize=True)],
            [Button.text("❌ انصراف و بازگشت به منوی اصلی", resize=True)]
        ]
        await event.respond(info_text, buttons=receipt_kb, parse_mode="html")
        return

    if text == "📤 ارسال فیش پرداخت":
        user_states[sender_id] = "waiting_for_receipt_plan"
        plans_kb = [
            [Button.text("🔸 پکیج ۱۵ روزه (۵۰ تومانی)", resize=True)],
            [Button.text("🔹 پکیج ۳۰ روزه (۱۰۰ تومانی)", resize=True)],
            [Button.text("❌ انصراف و بازگشت به منوی اصلی", resize=True)]
        ]
        await event.respond(f"{ICON_CARD} پلن انتخابی خود را مشخص کنید:", buttons=plans_kb, parse_mode="html")
        return

    if text == "📢 کانال‌های ثبت‌شده من":
        chans = get_user_channels(sender_id)
        if not chans:
            await event.respond(f"{ICON_ALERT} هنوز هیچ کانالی ثبت نکرده‌اید.", buttons=main_reply_kb(sender_id), parse_mode="html")
            return
        
        msg = f"{ICON_VERIFIED} <b>لیست کانال‌های متصل شما:</b>\n\nبرای حذف کانال و خروج اکانت، روی دکمه مربوطه بزنید:"
        kb = []
        for cid, title, uname in chans:
            kb.append([Button.inline(f"🗑 حذف کانال: {title}", f"del_chan:{cid}".encode())])
        kb.append([Button.text("❌ انصراف و بازگشت به منوی اصلی", resize=True)])
        
        await event.respond(msg, buttons=kb, parse_mode="html")
        return

    if text == "➕ ثبت و اتصال کانال":
        if not check_subscription(sender_id):
            await event.respond(f"{ICON_ALERT} اشتراک شما به پایان رسیده است. ابتدا حساب خود را شارژ کنید.", buttons=main_reply_kb(sender_id), parse_mode="html")
            return
        
        with sqlite3.connect("bot_data.db") as conn:
            user_info = conn.execute("SELECT max_channels FROM users WHERE user_id = ?", (sender_id,)).fetchone()
        max_ch = user_info[0] if user_info else 2
        curr_ch_count = len(get_user_channels(sender_id))

        if curr_ch_count >= max_ch:
            await event.respond(
                f"{ICON_ALERT} ظرفیت کانال‌های شما تکمیل شده است ({curr_ch_count} از {max_ch}).\n"
                "برای افزایش ظرفیت، می‌توانید مجدداً اشتراک تهیه کنید.",
                buttons=main_reply_kb(sender_id), parse_mode="html"
            )
            return

        user_states[sender_id] = "waiting_for_channel"
        await event.respond(f"{ICON_ROCKET} لینک خصوصی یا آیدی کانال خود (مثال: @channel) را ارسال کنید:", buttons=cancel_reply_kb(), parse_mode="html")
        return

    if text == "✍️ ارسال پست با ایموجی":
        if not check_subscription(sender_id):
            await event.respond(f"{ICON_ALERT} اشتراک شما منقضی شده است. ابتدا نسبت به تمدید آن اقدام کنید.", buttons=main_reply_kb(sender_id), parse_mode="html")
            return
        user_states[sender_id] = "waiting_for_post"
        prompt = (
            f"{ICON_ROCKET} <b>متن، عکس یا ویدیوی خود را ارسال کنید:</b>\n\n"
            "کدهای ایموجی را مانند <code>:a1:</code> درون متن قرار دهید.\n"
            "برای مشاهده کدها می‌توانید از دکمه زیر استفاده کنید:"
        )
        await event.respond(prompt, buttons=post_prompt_kb(), parse_mode="html")
        return

    if text == "📨 درخواست افزودن ایموجی دلخواه":
        user_states[sender_id] = "waiting_for_emoji_request"
        await event.respond(
            f"{ICON_CROWN} پستی که حاوی ایموجی مورد نظر شماست را به اینجا <b>فوروارد</b> کنید تا بررسی و اضافه شود:",
            buttons=cancel_reply_kb(),
            parse_mode="html"
        )
        return

    if text == "👑 پنل مدیریت" and is_admin(sender_id):
        await event.respond(f"{ICON_CROWN} پنل مدیریت:", buttons=admin_reply_kb(), parse_mode="html")
        return

    state = user_states.get(sender_id)

    if state == "waiting_for_emoji_request":
        user_states.pop(sender_id, None)
        await bot_client.send_message(
            ADMIN_ID,
            f"📨 <b>درخواست ایموجی جدید:</b>\n"
            f"▫️ نام: {event.sender.first_name if event.sender else 'نامشخص'}\n"
            f"▫️ یوزرنیم: @{event.sender.username if event.sender and event.sender.username else 'ندارد'}\n"
            f"▫️ آیدی عددی: <code>{sender_id}</code>",
            parse_mode="html"
        )
        await bot_client.forward_messages(ADMIN_ID, event.message)
        await event.respond(f"{ICON_VERIFIED} درخواست شما ثبت و برای مدیریت ارسال شد.", buttons=main_reply_kb(sender_id), parse_mode="html")
        return

    if state == "waiting_for_receipt_plan":
        if text in ["🔸 پکیج ۱۵ روزه (۵۰ تومانی)", "🔹 پکیج ۳۰ روزه (۱۰۰ تومانی)"]:
            user_drafts[sender_id] = {"selected_plan": text}
            user_states[sender_id] = "waiting_for_receipt_coupon"
            await event.respond("اگر کد تخفیف دارید آن را ارسال کنید، در غیر این صورت روی «بدون کد تخفیف» بزنید:", buttons=[[Button.text("بدون کد تخفیف", resize=True)], [Button.text("❌ انصراف و بازگشت به منوی اصلی", resize=True)]], parse_mode="html")
        else:
            await event.respond("لطفاً یکی از گزینه‌های موجود در کیبورد را انتخاب کنید.", parse_mode="html")
        return

    if state == "waiting_for_receipt_coupon":
        coupon_code = text if text != "بدون کد تخفیف" else ""
        user_drafts[sender_id]["coupon"] = coupon_code
        user_states[sender_id] = "waiting_for_receipt_photo"
        await event.respond(f"{ICON_CARD} تصویر فیش واریزی خود را ارسال کنید:", buttons=cancel_reply_kb(), parse_mode="html")
        return

    if state == "waiting_for_receipt_photo":
        if not event.photo:
            await event.respond(f"{ICON_ALERT} لطفاً فیش را به صورت عکس (Photo) ارسال کنید:", parse_mode="html")
            return
        
        draft = user_drafts.get(sender_id, {})
        plan = draft.get("selected_plan", "پکیج ۳۰ روزه")
        coupon = draft.get("coupon", "")
        
        days = 15 if "۱۵" in plan else 30
        price_str = "۵۰,۰۰۰ تومان" if "۱۵" in plan else "۱۰۰,۰۰۰ تومان"

        discount_text = ""
        if coupon:
            with sqlite3.connect("bot_data.db") as conn:
                c_data = conn.execute("SELECT discount_percent, max_uses, used_count FROM coupons WHERE code = ?", (coupon,)).fetchone()
            if c_data:
                percent, max_u, used_u = c_data
                if max_u == 0 or used_u < max_u:
                    discount_text = f"\n🎟 کد تخفیف: {coupon} ({percent}% تخفیف)"
                else:
                    discount_text = f"\n🎟 کد تخفیف: {coupon} (منقضی شده)"
            else:
                discount_text = f"\n🎟 کد تخفیف: {coupon} (نامعتبر)"

        user_states.pop(sender_id, None)
        user_drafts.pop(sender_id, None)
        await event.respond(f"{ICON_VERIFIED} فیش شما دریافت شد و پس از بررسی توسط ادمین تایید خواهد شد.", buttons=main_reply_kb(sender_id), parse_mode="html")
        
        sender = event.sender
        s_name = sender.first_name if sender else "نامشخص"
        s_uname = f"@{sender.username}" if sender and sender.username else "ندارد"

        caption = (
            f"💳 <b>فیش پرداخت جدید:</b>\n"
            f"👤 نام: {s_name}\n"
            f"🔗 یوزرنیم: {s_uname}\n"
            f"🆔 آیدی عددی: <code>{sender_id}</code>\n"
            f"📦 پلن: {plan} ({price_str}){discount_text}"
        )
        
        with sqlite3.connect("bot_data.db") as conn:
            all_admins = [row[0] for row in conn.execute("SELECT user_id FROM admins").fetchall()]
        
        kb = [
            [
                Button.inline("✅ تایید (+۲ کانال)", f"adm_app_{sender_id}_{days}".encode()),
                Button.inline("❌ رد فیش", f"adm_rej_{sender_id}".encode())
            ]
        ]

        for adm in all_admins:
            try:
                await bot_client.send_message(adm, caption, file=event.photo, buttons=kb, parse_mode="html")
            except Exception:
                pass
        return

    if state == "waiting_for_channel":
        status_msg = await event.respond("⏳ در حال اتصال و بررسی کانال...")
        try:
            target_entity = None
            if "t.me/+" in text or "t.me/joinchat/" in text:
                invite_hash = text.split("+")[-1].split("/")[-1].strip()
                try:
                    res = await user_client(ImportChatInviteRequest(invite_hash))
                    target_entity = res.chats[0]
                except UserAlreadyParticipantError:
                    clean_url = text.replace("https://", "").replace("http://", "")
                    target_entity = await user_client.get_entity(clean_url)
            else:
                clean_target = text.replace("https://t.me/", "").replace("http://t.me/", "").strip()
                try:
                    await user_client(JoinChannelRequest(clean_target))
                except UserAlreadyParticipantError:
                    pass
                except FloodWaitError as e:
                    await bot_client.send_message(
                        ADMIN_ID,
                        f"{ICON_ALERT} <b>محدودیت اکانت (FloodWait):</b>\nاکانت پرمیوم هنگام عضویت در <code>{clean_target}</code> به مدت {e.seconds} ثانیه محدود شد.",
                        parse_mode="html"
                    )
                    raise e
                target_entity = await user_client.get_entity(clean_target)

            has_post_rights = await verify_admin_rights(target_entity)
            chan_id = target_entity.id
            if not str(chan_id).startswith("-100"):
                chan_id = int(f"-100{chan_id}")
            title = getattr(target_entity, 'title', 'کانال')
            username = f"@{target_entity.username}" if getattr(target_entity, 'username', None) else str(chan_id)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if has_post_rights:
                with sqlite3.connect("bot_data.db") as conn:
                    conn.execute("INSERT INTO channels (user_id, channel_id, title, username, added_at) VALUES (?, ?, ?, ?, ?)", (sender_id, chan_id, title, username, now_str))
                    conn.execute("DELETE FROM pending_checks WHERE channel_id = ? AND user_id = ?", (chan_id, sender_id))
                
                sender = event.sender
                s_uname = f"@{sender.username}" if sender and sender.username else "ندارد"
                log_msg = (
                    f"📝 <b>ثبت کانال جدید:</b>\n"
                    f"👤 کاربر: {sender.first_name if sender else 'نامشخص'} ({s_uname})\n"
                    f"🆔 آیدی: <code>{sender_id}</code>\n"
                    f"📢 کانال: <b>{title}</b> (<code>{username}</code>)"
                )
                await bot_client.send_message(ADMIN_ID, log_msg, parse_mode="html")

                await status_msg.edit(f"{ICON_VERIFIED} کانال «{title}» با موفقیت ثبت شد.", parse_mode="html")
                await event.respond("منوی اصلی:", buttons=main_reply_kb(sender_id))
                user_states.pop(sender_id, None)
            else:
                with sqlite3.connect("bot_data.db") as conn:
                    conn.execute("INSERT OR REPLACE INTO pending_checks (channel_id, user_id, added_time) VALUES (?, ?, ?)", (chan_id, sender_id, now_str))
                
                check_again_kb = [[Button.inline("🔄 ادمین کردم، بررسی مجدد", f"check_admin_again:{chan_id}".encode())]]
                await status_msg.edit(
                    f"{ICON_ALERT} اکانت وارد کانال «{title}» شد اما دسترسی ادمین ندارد.\n"
                    "لطفاً دسترسی ارسال پیام (Post Messages) را فعال کرده و روی دکمه زیر بزنید:",
                    buttons=check_again_kb,
                    parse_mode="html"
                )
                await event.respond("منوی اصلی:", buttons=main_reply_kb(sender_id))
                user_states.pop(sender_id, None)
        except Exception as e:
            await status_msg.edit(f"❌ خطا در ارتباط با کانال:\n{str(e)}")
            await event.respond("منوی اصلی:", buttons=main_reply_kb(sender_id))
            user_states.pop(sender_id, None)
        return

    if text == "✏️ ویرایش متن و مدیا":
        user_states[sender_id] = "waiting_for_post"
        user_drafts.pop(sender_id, None)
        await event.respond("متن یا فایل جدید خود را ارسال کنید:", buttons=cancel_reply_kb(), parse_mode="html")
        return

    if state == "waiting_for_post":
        html_preview = build_html_for_preview(raw_content) if raw_content else ""
        user_text, entities = build_entities_for_user(raw_content) if raw_content else ("", [])

        user_drafts[sender_id] = {
            "media": event.media,
            "user_text": user_text,
            "entities": entities
        }

        await event.respond(f"{ICON_ROCKET} <b>پیش‌نمایش پست:</b>", parse_mode="html")
        if event.media:
            await bot_client.send_message(event.chat_id, message=html_preview, file=event.media, parse_mode="html")
        else:
            await bot_client.send_message(event.chat_id, message=html_preview, parse_mode="html")

        channels = get_user_channels(sender_id)
        if not channels:
            await event.respond(f"{ICON_ALERT} هیچ کانالی ثبت نکرده‌اید. ابتدا کانال خود را متصل کنید.", buttons=main_reply_kb(sender_id), parse_mode="html")
            user_states.pop(sender_id, None)
            return

        user_states[sender_id] = "choosing_channel"
        kb = [[Button.text(f"📢 {title}", resize=True)] for _, title, _ in channels]
        kb.append([Button.text("✏️ ویرایش متن و مدیا", resize=True), Button.text("❌ انصراف و بازگشت به منوی اصلی", resize=True)])
        await event.respond("کانال مقصد را برای انتشار انتخاب کنید:", buttons=kb)
        return

    if state == "choosing_channel":
        channels = get_user_channels(sender_id)
        selected_chan = None
        for cid, title, _ in channels:
            if text == f"📢 {title}":
                selected_chan = cid
                break

        if not selected_chan:
            await event.respond("کانال انتخابی معتبر نیست. از دکمه‌های زیر استفاده کنید:", buttons=cancel_reply_kb())
            return

        draft = user_drafts.get(sender_id)
        if not draft:
            await event.respond(f"{ICON_ALERT} پیش‌نویس پست منقضی شده است. لطفاً مجدداً ارسال کنید.", buttons=main_reply_kb(sender_id), parse_mode="html")
            user_states.pop(sender_id, None)
            return

        status = await event.respond("⏳ در حال ارسال به کانال...")
        file_path = None
        try:
            target_entity = await user_client.get_input_entity(selected_chan)
            
            if draft.get("media"):
                await status.edit("⏳ در حال پردازش فایل رسانه...")
                file_path = await bot_client.download_media(draft["media"])

            await user_client.send_message(
                target_entity,
                message=draft["user_text"],
                formatting_entities=draft["entities"],
                file=file_path
            )
            await status.edit(f"{ICON_ROCKET} <b>پست با موفقیت در کانال منتشر شد.</b>", parse_mode="html")
            await event.respond("منوی اصلی:", buttons=main_reply_kb(sender_id))
        except Exception as e:
            await status.edit(f"❌ خطا در ارسال پست:\n{str(e)}")
            await event.respond("منوی اصلی:", buttons=main_reply_kb(sender_id))
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

        user_drafts.pop(sender_id, None)
        user_states.pop(sender_id, None)
        return

    if is_admin(sender_id):
        if text == "📊 آمار کلی ربات":
            with sqlite3.connect("bot_data.db") as conn:
                tot_u = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                act_u = conn.execute("SELECT COUNT(*) FROM users WHERE expire_date > datetime('now')").fetchone()[0]
                ban_u = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1").fetchone()[0]
                tot_c = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
                tot_e = conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
            msg = (
                f"{ICON_CROWN} <b>آمار ربات:</b>\n\n"
                f"👥 کل کاربران: <code>{tot_u}</code>\n"
                f"⚡️ اشتراک‌های فعال: <code>{act_u}</code>\n"
                f"⛔️ کاربران مسدود: <code>{ban_u}</code>\n"
                f"📢 کانال‌های ثبت‌شده: <code>{tot_c}</code>\n"
                f"🎨 تعداد ایموجی‌ها: <code>{tot_e}</code>"
            )
            await event.respond(msg, buttons=admin_reply_kb(), parse_mode="html")
            return

        elif text == "📢 لیست کانال‌های متصل اکانت":
            status = await event.respond("⏳ در حال دریافت لیست کانال‌ها...")
            dialogs = await user_client.get_dialogs()
            channels_list = []
            for d in dialogs:
                if d.is_channel:
                    channels_list.append(f"• <b>{d.title}</b> (<code>{d.id}</code>)")
            
            if not channels_list:
                await status.edit("اکانت در هیچ کانالی عضو نیست.")
            else:
                out = f"{ICON_VERIFIED} <b>کانال‌های متصل به اکانت:</b>\n\n" + "\n".join(channels_list[:30])
                await status.edit(out, parse_mode="html")
            await event.respond("پنل مدیریت:", buttons=admin_reply_kb())
            return

        elif text == "➕ افزودن کل پک ایموجی":
            user_states[sender_id] = "admin_add_pack"
            await event.respond(f"{ICON_ROCKET} لینک پک ایموجی (مثال: https://t.me/addemoji/...) را ارسال کنید:", buttons=cancel_reply_kb(), parse_mode="html")
            return

        elif text == "👤 تنظیم اشتراک کاربر":
            user_states[sender_id] = "admin_set_sub"
            await event.respond("آیدی عددی کاربر و تعداد روز را با فاصله بفرستید:\nمثال: <code>123456789 30</code>", buttons=cancel_reply_kb(), parse_mode="html")
            return

        elif text == "📩 ارسال پیام به کاربر":
            user_states[sender_id] = "admin_msg_user"
            await event.respond("آیدی عددی و متن پیام را با « - » جدا کنید:\nمثال:\n<code>123456789 - متن پیام</code>", buttons=cancel_reply_kb(), parse_mode="html")
            return

        elif text == "📨 پیام همگانی":
            user_states[sender_id] = "admin_broadcast"
            await event.respond("پیام خود را برای ارسال به تمامی کاربران بفرستید:", buttons=cancel_reply_kb(), parse_mode="html")
            return

        elif text == "🔗 تنظیم جوین اجباری":
            user_states[sender_id] = "admin_set_force_join"
            curr_fj = get_setting("force_join_channel") or "غیرفعال"
            await event.respond(f"کانال فعلی: <b>{curr_fj}</b>\nآیدی کانال جدید (مثال: @channel) یا کلمه <code>off</code> را ارسال کنید:", buttons=cancel_reply_kb(), parse_mode="html")
            return

        elif text == "🎟 مدیریت کدهای تخفیف":
            user_states[sender_id] = "admin_add_coupon"
            await event.respond("کد تخفیف، درصد و سقف استفاده را با فاصله بفرستید:\nمثال: <code>OFF20 20 50</code>", buttons=cancel_reply_kb(), parse_mode="html")
            return

        elif text == "➕ افزودن ادمین جدید":
            if sender_id != ADMIN_ID:
                await event.respond("این بخش فقط مخصوص مدیر اصلی ربات است.", buttons=admin_reply_kb())
                return
            user_states[sender_id] = "admin_add_new_admin"
            await event.respond("آیدی عددی ادمین جدید را وارد کنید:", buttons=cancel_reply_kb(), parse_mode="html")
            return

        elif text == "🚫 مسدود کردن (Ban)":
            user_states[sender_id] = "admin_ban_user"
            await event.respond("آیدی عددی کاربر را جهت مسدودسازی بفرستید:", buttons=cancel_reply_kb())
            return

        elif text == "✅ رفع مسدودیت (Unban)":
            user_states[sender_id] = "admin_unban_user"
            await event.respond("آیدی عددی کاربر را جهت رفع مسدودیت بفرستید:", buttons=cancel_reply_kb())
            return

        if state == "admin_add_pack":
            status = await event.respond("⏳ در حال دریافت اطلاعات پک...")
            pack_short_name = None

            link_match = re.search(r"(?:t\.me/addemoji/)([a-zA-Z0-9_]+)", text)
            if link_match:
                pack_short_name = link_match.group(1)
            elif event.entities:
                for ent in event.entities:
                    if isinstance(ent, MessageEntityCustomEmoji):
                        try:
                            msg_obj = await bot_client.get_messages(event.chat_id, ids=event.message.id)
                            for attr in getattr(msg_obj, 'media', None).document.attributes if getattr(msg_obj, 'media', None) else []:
                                if hasattr(attr, 'stickerset') and hasattr(attr.stickerset, 'short_name'):
                                    pack_short_name = attr.stickerset.short_name
                                    break
                        except Exception:
                            pass
                        break

            if pack_short_name:
                try:
                    sticker_set = await user_client(GetStickerSetRequest(
                        stickerset=InputStickerSetShortName(short_name=pack_short_name),
                        hash=0
                    ))
                    
                    aliases = get_aliases()
                    clean_prefix = re.sub(r"[^a-zA-Z]", "", pack_short_name)[:4].lower() or "pkg"
                    start_num = len([a for a in aliases if a[0].startswith(clean_prefix)]) + 1
                    
                    new_entries = []
                    for i, doc in enumerate(sticker_set.documents):
                        code = f"{clean_prefix}{start_num + i}"
                        new_entries.append((code, str(doc.id), sticker_set.set.title))

                    with sqlite3.connect("bot_data.db") as conn:
                        conn.executemany("INSERT OR REPLACE INTO aliases (code, emoji_id, name) VALUES (?, ?, ?)", new_entries)

                    await status.edit(
                        f"{ICON_VERIFIED} پک «{sticker_set.set.title}» اضافه شد.\n\n"
                        f"📦 تعداد: {len(new_entries)}\n"
                        f"🏷 کدها: <code>:{clean_prefix}{start_num}:</code> تا <code>:{clean_prefix}{start_num + len(new_entries) - 1}:</code>",
                        parse_mode="html"
                    )
                    await event.respond("پنل مدیریت:", buttons=admin_reply_kb())
                    user_states.pop(sender_id, None)
                    return
                except Exception as e:
                    await status.edit(f"❌ خطا در دریافت پک:\n{str(e)}")
                    await event.respond("پنل مدیریت:", buttons=admin_reply_kb())
                    user_states.pop(sender_id, None)
                    return

            await status.edit(f"{ICON_ALERT} لینک پک معتبر نیست.", parse_mode="html")
            return

        if state == "admin_set_sub":
            parts = text.split()
            if len(parts) != 2 or not parts[0].isdigit():
                await event.respond("فرمت نادرست است. مثال: 123456789 30")
                return
            t_uid, days = int(parts[0]), int(parts[1])
            new_date = add_subscription_days(t_uid, days, extra_channels_add=2)
            user_states.pop(sender_id, None)
            await event.respond(f"{ICON_VERIFIED} اشتراک کاربر <code>{t_uid}</code> تا <code>{new_date}</code> تمدید شد.", buttons=admin_reply_kb(), parse_mode="html")
            try:
                await bot_client.send_message(t_uid, f"{ICON_CROWN} اشتراک شما به مدت {days} روز توسط مدیریت شارژ شد.", parse_mode="html")
            except Exception:
                pass
            return

        if state == "admin_msg_user":
            parts = text.split(" - ", 1)
            if len(parts) != 2 or not parts[0].strip().isdigit():
                await event.respond("فرمت نادرست است. مثال:\n123456789 - متن پیام")
                return
            t_uid, msg_text = int(parts[0].strip()), parts[1].strip()
            user_states.pop(sender_id, None)
            try:
                await bot_client.send_message(t_uid, f"📩 <b>پیام از طرف پشتیبانی:</b>\n\n{msg_text}", parse_mode="html")
                await event.respond(f"{ICON_VERIFIED} پیام ارسال شد.", buttons=admin_reply_kb(), parse_mode="html")
            except Exception as e:
                await event.respond(f"❌ خطا در ارسال: {str(e)}", buttons=admin_reply_kb())
            return

        if state == "admin_broadcast":
            user_states.pop(sender_id, None)
            status = await event.respond("⏳ در حال ارسال پیام همگانی...")
            with sqlite3.connect("bot_data.db") as conn:
                all_users = [row[0] for row in conn.execute("SELECT user_id FROM users").fetchall()]
            
            success_count = 0
            for uid in all_users:
                try:
                    if event.message.media:
                        await bot_client.send_message(uid, message=event.message.message, file=event.message.media)
                    else:
                        await bot_client.send_message(uid, message=event.message.message, parse_mode="html")
                    success_count += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    pass
            
            await status.edit(f"{ICON_VERIFIED} پیام همگانی به <b>{success_count}</b> کاربر ارسال شد.", parse_mode="html")
            await event.respond("پنل مدیریت:", buttons=admin_reply_kb())
            return

        if state == "admin_set_force_join":
            user_states.pop(sender_id, None)
            if text.lower() == "off":
                set_setting("force_join_channel", "")
                await event.respond(f"{ICON_VERIFIED} عضویت اجباری غیرفعال شد.", buttons=admin_reply_kb(), parse_mode="html")
            else:
                set_setting("force_join_channel", text)
                await event.respond(f"{ICON_VERIFIED} کانال عضویت اجباری تنظیم شد: <b>{text}</b>", buttons=admin_reply_kb(), parse_mode="html")
            return

        if state == "admin_add_coupon":
            parts = text.split()
            if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                await event.respond("فرمت نادرست است. مثال: OFF20 20 50")
                return
            c_code, percent, max_u = parts[0].upper(), int(parts[1]), int(parts[2])
            with sqlite3.connect("bot_data.db") as conn:
                conn.execute("INSERT OR REPLACE INTO coupons (code, discount_percent, max_uses, used_count) VALUES (?, ?, ?, 0)", (c_code, percent, max_u))
                conn.commit()
            user_states.pop(sender_id, None)
            await event.respond(f"{ICON_VERIFIED} کد تخفیف <b>{c_code}</b> ثبت شد.", buttons=admin_reply_kb(), parse_mode="html")
            return

        if state == "admin_add_new_admin":
            if not text.isdigit():
                await event.respond("فقط آیدی عددی ارسال کنید:")
                return
            new_adm_id = int(text)
            with sqlite3.connect("bot_data.db") as conn:
                conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_adm_id,))
                conn.commit()
            user_states.pop(sender_id, None)
            await event.respond(f"{ICON_VERIFIED} کاربر <code>{new_adm_id}</code> به لیست ادمین‌ها اضافه شد.", buttons=admin_reply_kb(), parse_mode="html")
            return

        if state == "admin_ban_user":
            if not text.isdigit():
                await event.respond("فقط آیدی عددی ارسال کنید:")
                return
            set_ban_status(int(text), 1)
            user_states.pop(sender_id, None)
            await event.respond(f"{ICON_ALERT} کاربر {text} مسدود شد.", buttons=admin_reply_kb(), parse_mode="html")
            return

        if state == "admin_unban_user":
            if not text.isdigit():
                await event.respond("فقط آیدی عددی ارسال کنید:")
                return
            set_ban_status(int(text), 0)
            user_states.pop(sender_id, None)
            await event.respond(f"{ICON_VERIFIED} کاربر {text} رفع مسدودیت شد.", buttons=admin_reply_kb(), parse_mode="html")
            return


@bot_client.on(events.CallbackQuery)
async def on_callback(event):
    data = event.data.decode("utf-8")
    sender_id = event.sender_id

    if data == "noop":
        await event.answer()
        return

    if data == "close_catalog":
        await event.delete()
        return

    if data.startswith("page_packs:"):
        p = int(data.split(":")[1])
        text, kb = render_packs_catalog(page=p)
        if kb:
            await event.edit(text, buttons=kb, parse_mode="html")
        else:
            await event.answer("بسته‌ای یافت نشد.", alert=True)
        return

    if data.startswith("view_pack:"):
        _, prefix, p_str = data.split(":")
        text, kb = render_single_pack(prefix=prefix, page=int(p_str))
        await event.edit(text, buttons=kb, parse_mode="html")
        return

    if data.startswith("del_chan:"):
        target_cid = int(data.split(":")[1])
        try:
            target_entity = await user_client.get_entity(target_cid)
            await user_client(LeaveChannelRequest(target_entity))
        except Exception:
            pass
        
        with sqlite3.connect("bot_data.db") as conn:
            conn.execute("DELETE FROM channels WHERE channel_id = ? AND user_id = ?", (target_cid, sender_id))
            conn.execute("DELETE FROM pending_checks WHERE channel_id = ? AND user_id = ?", (target_cid, sender_id))
            
        await event.edit(f"{ICON_VERIFIED} کانال حذف شد و اکانت از آن خارج گردید.", parse_mode="html")
        return

    if data.startswith("check_admin_again:"):
        target_cid = int(data.split(":")[1])
        try:
            target_entity = await user_client.get_entity(target_cid)
            has_post_rights = await verify_admin_rights(target_entity)
            if has_post_rights:
                title = getattr(target_entity, 'title', 'کانال')
                username = f"@{target_entity.username}" if getattr(target_entity, 'username', None) else str(target_cid)
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                with sqlite3.connect("bot_data.db") as conn:
                    conn.execute("INSERT INTO channels (user_id, channel_id, title, username, added_at) VALUES (?, ?, ?, ?, ?)", (sender_id, target_cid, title, username, now_str))
                    conn.execute("DELETE FROM pending_checks WHERE channel_id = ? AND user_id = ?", (target_cid, sender_id))
                
                await event.edit(f"{ICON_VERIFIED} دسترسی تایید و کانال «{title}» متصل شد.", parse_mode="html")
            else:
                await event.answer("هنوز دسترسی ارسال پست (Post Messages) فعال نشده است.", alert=True)
        except Exception as e:
            await event.answer(f"خطا: {str(e)}", alert=True)
        return

    if is_admin(sender_id):
        if data.startswith("adm_app_"):
            parts = data.split("_")
            target_uid = int(parts[2])
            days = int(parts[3])
            
            new_exp = add_subscription_days(target_uid, days, extra_channels_add=2)
            await event.edit(f"{ICON_VERIFIED} فیش کاربر <code>{target_uid}</code> تایید شد.", parse_mode="html")
            try:
                await bot_client.send_message(target_uid, f"{ICON_VERIFIED} پرداخت شما تایید شد. اشتراک {days} روزه تا تاریخ <code>{new_exp}</code> فعال گردید و ۲ کانال به ظرفیت شما اضافه شد.", parse_mode="html")
            except Exception:
                pass
        elif data.startswith("adm_rej_"):
            target_uid = int(data.replace("adm_rej_", ""))
            await event.edit(f"❌ فیش کاربر <code>{target_uid}</code> رد شد.", parse_mode="html")
            try:
                await bot_client.send_message(target_uid, f"{ICON_ALERT} فیش پرداختی شما مورد تایید قرار نگرفت.", parse_mode="html")
            except Exception:
                pass


async def check_pending_channels_task():
    while True:
        await asyncio.sleep(3600)
        try:
            with sqlite3.connect("bot_data.db") as conn:
                rows = conn.execute("SELECT channel_id, user_id, added_time FROM pending_checks").fetchall()
            
            for chan_id, user_id, added_time in rows:
                dt_added = datetime.strptime(added_time, "%Y-%m-%d %H:%M:%S")
                if datetime.now() - dt_added > timedelta(days=1):
                    has_rights = False
                    try:
                        target_entity = await user_client.get_entity(chan_id)
                        has_rights = await verify_admin_rights(target_entity)
                    except Exception:
                        pass

                    if not has_rights:
                        try:
                            target_entity = await user_client.get_entity(chan_id)
                            await user_client(LeaveChannelRequest(target_entity))
                        except Exception:
                            pass

                        with sqlite3.connect("bot_data.db") as conn:
                            conn.execute("DELETE FROM pending_checks WHERE channel_id = ? AND user_id = ?", (chan_id, user_id))

                        try:
                            await bot_client.send_message(
                                user_id,
                                f"{ICON_ALERT} به دلیل عدم اعطای دسترسی ادمین طی ۲۴ ساعت گذشته، اکانت از کانال شما خارج شد.",
                                parse_mode="html"
                            )
                        except Exception:
                            pass
        except Exception:
            pass


async def main():
    global bot_username
    if PASSWORD_2FA:
        await user_client.start(password=PASSWORD_2FA)
    else:
        await user_client.start()
        
    await bot_client.start(bot_token=BOT_TOKEN)
    
    bot_me = await bot_client.get_me()
    bot_username = bot_me.username

    asyncio.create_task(check_pending_channels_task())

    print(f"Bot @{bot_username} is running...")
    await asyncio.gather(
        bot_client.run_until_disconnected(),
        user_client.run_until_disconnected()
    )

if __name__ == "__main__":
    asyncio.run(main())