import io
import logging
import os
import re
import sys
import threading
from html import escape

import requests
import telebot
from PIL import Image, ImageDraw, ImageFilter
from telebot import types

# ==========================================
# ⚙️ CONFIGURATION & CREDENTIALS
# ==========================================
# আপনার দেওয়া Bot Token এবং Admin ID সেট করা হয়েছে
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8653246632:AAGxcMbU1Ao_xIV6hmQAeBtxswUpws2Ztp0")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8894441286"))

# ডাইনামিক গ্লোবাল স্টেট (অ্যাডমিন প্যানেল থেকে পরিবর্তনযোগ্য)
config = {
    "api_url": "https://app-creator-x.ueu.workers.dev/api/v1/build",
    "admin_password": "admin",  # ডিফল্ট পাসওয়ার্ড: admin
    "banned_users": set()       # ব্ল্যাকলিস্ট করা ইউজার আইডি
}

BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apk_converter_banner.png")

MAX_HTML_SIZE = 10 * 1024 * 1024
MAX_ZIP_SIZE = 10 * 1024 * 1024
MAX_ICON_SIZE = 6 * 1024 * 1024
MAX_ICON_PIXELS = 4_000_000
MAX_TOTAL_BODY = 16_842_752
MAX_TOTAL_FILES = MAX_TOTAL_BODY - 64 * 1024
MAX_APP_NAME_UTF16 = 80
MAX_APP_NAME_UTF8 = 120

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("apk_converter_bot")

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise SystemExit("Set a valid BOT_TOKEN environment variable before running.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
sessions = {}


def ensure_banner():
    if os.path.exists(BANNER_PATH):
        return
    w, h = 1200, 675
    im = Image.new("RGB", (w, h), "#10162d")
    px = im.load()
    for y in range(h):
        for x in range(w):
            t = (x / w + y / h) / 2
            px[x, y] = (int(15 + 18 * t), int(22 + 26 * t), int(53 + 55 * t))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((760, -160, 1320, 400), fill=(101, 88, 255, 95))
    gd.ellipse((-180, 410, 430, 1030), fill=(0, 207, 191, 55))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    im = Image.alpha_composite(im.convert("RGBA"), glow)
    d = ImageDraw.Draw(im)
    phone = (435, 85, 765, 590)
    d.rounded_rectangle(phone, radius=42, fill="#202a4b", outline="#6976b6", width=7)
    d.rounded_rectangle((458, 132, 742, 535), radius=14, fill="#0c1329")
    d.rounded_rectangle((555, 101, 645, 119), radius=9, fill="#6976b6")
    d.ellipse((579, 551, 621, 593), fill="#6976b6")
    d.rounded_rectangle((535, 230, 665, 390), radius=22, fill="#665cff")
    d.polygon([(600, 198), (646, 240), (554, 240)], fill="#8d86ff")
    d.line((574, 219, 560, 200), fill="#8d86ff", width=8)
    d.line((626, 219, 640, 200), fill="#8d86ff", width=8)
    d.ellipse((568, 278, 584, 294), fill="white")
    d.ellipse((616, 278, 632, 294), fill="white")
    d.rounded_rectangle((570, 330, 630, 347), radius=8, fill="#b9b5ff")
    im.convert("RGB").save(BANNER_PATH, "PNG", optimize=True)


# ==========================================
# 🔘 KEYBOARD MARKUPS
# ==========================================
def kb_main_menu(user_id=None):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🚀 Create APK", callback_data="menu:new"))
    kb.add(
        types.InlineKeyboardButton("📘 How it works", callback_data="menu:help"),
        types.InlineKeyboardButton("ℹ️ About", callback_data="menu:about"),
    )
    if user_id == ADMIN_ID:
        kb.add(types.InlineKeyboardButton("👑 Admin Panel", callback_data="admin:dashboard"))
    return kb


def kb_cancel():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✖️ Cancel", callback_data="flow:cancel"))
    return kb


def kb_back():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu:home"))
    return kb


def kb_confirm():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Build APK", callback_data="flow:build"),
        types.InlineKeyboardButton("✏️ Rename App", callback_data="flow:rename"),
    )
    kb.add(types.InlineKeyboardButton("✖️ Cancel", callback_data="flow:cancel"))
    return kb


def kb_admin():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🌐 Change API URL", callback_data="admin:change_api"),
        types.InlineKeyboardButton("🖼️ Change Logo", callback_data="admin:change_logo"),
    )
    kb.add(
        types.InlineKeyboardButton("🚫 Ban User", callback_data="admin:ban_user"),
        types.InlineKeyboardButton("✅ Unban User", callback_data="admin:unban_user"),
    )
    kb.add(
        types.InlineKeyboardButton("📜 Banned List", callback_data="admin:list_banned"),
        types.InlineKeyboardButton("🔐 Pass Change", callback_data="admin:change_pass"),
    )
    kb.add(types.InlineKeyboardButton("🧹 Clear Sessions", callback_data="admin:clear_sessions"))
    kb.add(types.InlineKeyboardButton("⬅️ Main Menu", callback_data="menu:home"))
    return kb


# ==========================================
# 🛠️ HELPER FUNCTIONS & MIDDLEWARE
# ==========================================
def reset(chat_id):
    sessions.pop(chat_id, None)
    bot.clear_step_handler_by_chat_id(chat_id)


def is_banned(user_id):
    return user_id in config["banned_users"]


def safe_name(value):
    return escape(value or "")


def progress(step, label):
    cells = ["✅" if i < step else ("🔵" if i == step else "⚪") for i in range(5)]
    return f"<b>Step {step + 1}/5 — {label}</b>\n{'  '.join(cells)}\n\n"


def validate_app_name(value):
    value = (value or "").strip()
    if not value:
        return "Please send a non-empty app name."
    utf16_units = len(value.encode("utf-16-le")) // 2
    utf8_bytes = len(value.encode("utf-8"))
    if utf16_units > MAX_APP_NAME_UTF16:
        return f"Name is too long ({utf16_units}/80 UTF-16 units)."
    if utf8_bytes > MAX_APP_NAME_UTF8:
        return f"Name is too large ({utf8_bytes}/120 UTF-8 bytes)."
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        return "The app name cannot contain control characters."
    return None


def validate_icon(content):
    if not content:
        return "The icon file is empty."
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in ("PNG", "JPEG"):
                return "Only PNG and JPEG icons are supported."
            width, height = image.size
            if width * height > MAX_ICON_PIXELS:
                return "The icon is too large: maximum is 4 megapixels."
            if image.format == "PNG" and image.info.get("interlace"):
                return "Interlaced PNG icons are not supported. Please export a regular PNG."
    except Exception:
        return "That file is not a valid PNG or JPEG image."
    return None


def send_home(chat_id, intro=True):
    reset(chat_id)
    if is_banned(chat_id):
        bot.send_message(chat_id, "🚫 <b>You are banned from using this bot.</b>")
        return

    text = (
        "<b>APK Converter Studio</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🌐 Website → 📱 Android APK\n\n"
        "Convert an HTML website or ZIP project into a test APK in a few simple steps.\n\n"
        "<i>Fast • Simple • Test-build ready</i>"
        if intro
        else "<b>APK Converter Studio</b>\n\nChoose an action below to continue:"
    )
    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as photo:
            bot.send_photo(chat_id, photo, caption=text, reply_markup=kb_main_menu(chat_id))
    else:
        bot.send_message(chat_id, text, reply_markup=kb_main_menu(chat_id))


def flow_screen(chat_id, text, markup=None, source=None):
    session = sessions.get(chat_id)
    if session is None:
        return None
    msg = source
    try:
        if msg is None:
            msg = bot.send_message(chat_id, text, reply_markup=markup)
        elif msg.content_type == "photo":
            bot.edit_message_caption(text, chat_id, msg.message_id, reply_markup=markup)
        else:
            bot.edit_message_text(text, chat_id, msg.message_id, reply_markup=markup)
    except Exception:
        msg = bot.send_message(chat_id, text, reply_markup=markup)
    session["ui_message"] = msg
    return msg


def flow_next(chat_id, text, handler, *args, source=None, markup=None):
    msg = flow_screen(chat_id, text, markup or kb_cancel(), source=source)
    bot.clear_step_handler_by_chat_id(chat_id)
    bot.register_next_step_handler(msg, handler, *args)
    return msg


def edit_ui_message(message, text, markup=None):
    try:
        if message.content_type == "photo":
            bot.edit_message_caption(text, message.chat.id, message.message_id, reply_markup=markup)
        else:
            bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup)
    except Exception as exc:
        logger.debug("UI status update failed: %s", exc)


def api_error_message(resp):
    try:
        payload = resp.json()
        detail = payload.get("error", resp.text)
    except ValueError:
        detail = resp.text or f"HTTP {resp.status_code}"
    status_help = {
        400: "Please check the app name and uploaded files.",
        413: "The complete request is too large. Try smaller files.",
        415: "The upload format was rejected. Please try again.",
        429: "The build service is rate-limited. Please wait a minute and retry.",
        500: "The build service failed unexpectedly. Please retry with smaller files.",
    }.get(resp.status_code, "Please try again later.")
    return f"{escape(str(detail))}\n\n<i>{status_help}</i>"


def animate_build(message, stop_event):
    frames = ["◐", "◓", "◑", "◒"]
    stages = [
        ("Uploading website", "📤", 25),
        ("Preparing WebView", "🧩", 45),
        ("Compiling Android package", "⚙️", 70),
        ("Signing test build", "🔐", 88),
        ("Finalizing APK", "📦", 96),
    ]
    index = 0
    while not stop_event.is_set():
        label, icon, percent = stages[index % len(stages)]
        filled = max(1, percent // 10)
        bar = "🟦" * filled + "▫️" * (10 - filled)
        text = (
            f"<b>{frames[index % len(frames)]} APK Converter Studio</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<b>BUILD IN PROGRESS</b>\n\n"
            f"{icon} <b>{label}</b>\n"
            f"{bar} <b>{percent}%</b>\n\n"
            "☕ Please wait while the build service works…\n"
            "<i>Your files are being processed securely.</i>"
        )
        edit_ui_message(message, text)
        index += 1
        stop_event.wait(1.5)


HELP_TEXT = (
    "<b>📘 How it works</b>\n\n"
    "<b>1.</b> Click <b>Create APK</b>\n"
    "<b>2.</b> Enter app name (1–80 characters)\n"
    "<b>3.</b> Upload <code>.html</code> or <code>.zip</code> (Max 10 MiB)\n"
    "<b>4.</b> Upload PNG/JPEG icon (Max 6 MiB)\n"
    "<b>5.</b> Review and click <b>Build APK</b>"
)

ABOUT_TEXT = (
    "<b>ℹ️ About APK Converter Studio</b>\n\n"
    "A clean Telegram interface built over APK CreatorX build API.\n"
    "Target OS: Android 6.0+"
)


# ==========================================
# 👑 ADMIN PANEL CONTROL & ACTIONS
# ==========================================
@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID:
        bot.send_message(chat_id, "🚫 <b>Access Denied:</b> Only Admin can access this command.")
        return
    msg = bot.send_message(chat_id, "🔐 <b>Enter Admin Password:</b>")
    bot.register_next_step_handler(msg, verify_admin_pass)


def verify_admin_pass(message):
    chat_id = message.chat.id
    if message.text == config["admin_password"]:
        show_admin_panel(chat_id)
    else:
        bot.send_message(chat_id, "❌ <b>Wrong Password!</b> Access denied.")


def show_admin_panel(chat_id, message_id=None):
    text = (
        "<b>👑 Admin Control Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 <b>Current API URL:</b> <code>{config['api_url']}</code>\n"
        f"🚫 <b>Total Banned Users:</b> <code>{len(config['banned_users'])}</code>\n"
        f"👥 <b>Active Sessions:</b> <code>{len(sessions)}</code>\n"
        f"🔐 <b>Current Password:</b> <code>{config['admin_password']}</code>\n"
        f"🖼️ <b>Logo Status:</b> <code>{'Custom' if os.path.exists(BANNER_PATH) else 'Default'}</code>\n\n"
        "Select an action below:"
    )
    if message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=kb_admin())
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=kb_admin())


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin:"))
def cb_admin_actions(call):
    chat_id = call.message.chat.id
    if chat_id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Unauthorized Action", show_alert=True)
        return

    action = call.data.split(":")[1]

    if action == "dashboard":
        show_admin_panel(chat_id, call.message.message_id)
    elif action == "clear_sessions":
        sessions.clear()
        bot.answer_callback_query(call.id, "All Sessions Cleared!", show_alert=True)
        show_admin_panel(chat_id, call.message.message_id)
    elif action == "change_api":
        msg = bot.send_message(chat_id, "🌐 <b>Send the new API URL:</b>\nCurrent: <code>" + config["api_url"] + "</code>")
        bot.register_next_step_handler(msg, admin_set_api)
    elif action == "change_pass":
        msg = bot.send_message(chat_id, "🔐 <b>Send new Admin Password:</b>")
        bot.register_next_step_handler(msg, admin_set_pass)
    elif action == "ban_user":
        msg = bot.send_message(chat_id, "🚫 <b>Send Telegram User ID to Ban:</b>")
        bot.register_next_step_handler(msg, admin_ban_user)
    elif action == "unban_user":
        msg = bot.send_message(chat_id, "✅ <b>Send Telegram User ID to Unban:</b>")
        bot.register_next_step_handler(msg, admin_unban_user)
    elif action == "list_banned":
        banned_list = "\n".join([f"• <code>{uid}</code>" for uid in config["banned_users"]]) or "No banned users."
        bot.send_message(chat_id, f"📜 <b>Banned Users List:</b>\n\n{banned_list}", reply_markup=kb_admin())
    elif action == "change_logo":
        msg = bot.send_message(chat_id, "🖼️ <b>Send a photo to set as new Bot Banner/Logo:</b>")
        bot.register_next_step_handler(msg, admin_set_logo)


def admin_set_api(message):
    new_url = (message.text or "").strip()
    if new_url.startswith("http://") or new_url.startswith("https://"):
        config["api_url"] = new_url
        bot.send_message(message.chat.id, "✅ <b>API URL Updated successfully!</b>")
    else:
        bot.send_message(message.chat.id, "❌ Invalid URL format.")
    show_admin_panel(message.chat.id)


def admin_set_pass(message):
    new_pass = (message.text or "").strip()
    if new_pass:
        config["admin_password"] = new_pass
        bot.send_message(message.chat.id, f"✅ <b>Password changed to:</b> <code>{new_pass}</code>")
    else:
        bot.send_message(message.chat.id, "❌ Password cannot be empty.")
    show_admin_panel(message.chat.id)


def admin_ban_user(message):
    try:
        uid = int(message.text.strip())
        config["banned_users"].add(uid)
        bot.send_message(message.chat.id, f"✅ <b>User {uid} has been banned!</b>")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid User ID.")
    show_admin_panel(message.chat.id)


def admin_unban_user(message):
    try:
        uid = int(message.text.strip())
        config["banned_users"].discard(uid)
        bot.send_message(message.chat.id, f"✅ <b>User {uid} has been unbanned!</b>")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid User ID.")
    show_admin_panel(message.chat.id)


def admin_set_logo(message):
    if not message.photo:
        bot.send_message(message.chat.id, "❌ Please send an image photo.")
        show_admin_panel(message.chat.id)
        return
    try:
        file_id = message.photo[-1].file_id
        info = bot.get_file(file_id)
        content = bot.download_file(info.file_path)
        with open(BANNER_PATH, "wb") as f:
            f.write(content)
        bot.send_message(message.chat.id, "🎉 <b>Logo/Banner updated successfully!</b>")
    except Exception as exc:
        bot.send_message(message.chat.id, f"❌ Failed to update logo: {exc}")
    show_admin_panel(message.chat.id)


# ==========================================
# 🤖 MAIN BOT FLOW HANDLERS
# ==========================================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(message):
    send_home(message.chat.id)


@bot.message_handler(commands=["help"])
def cmd_help(message):
    reset(message.chat.id)
    bot.send_message(message.chat.id, HELP_TEXT, reply_markup=kb_back())


@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    reset(message.chat.id)
    bot.send_message(message.chat.id, "✖️ <b>Cancelled</b>", reply_markup=kb_main_menu(message.chat.id))


@bot.message_handler(commands=["newapk"])
def cmd_newapk(message):
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, "🚫 You are banned.")
        return
    start_flow(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "menu:home")
def cb_home(call):
    bot.answer_callback_query(call.id)
    send_home(call.message.chat.id, intro=False)


@bot.callback_query_handler(func=lambda c: c.data == "menu:help")
def cb_help(call):
    bot.answer_callback_query(call.id)
    reset(call.message.chat.id)
    edit_ui_message(call.message, HELP_TEXT, kb_back())


@bot.callback_query_handler(func=lambda c: c.data == "menu:about")
def cb_about(call):
    bot.answer_callback_query(call.id)
    reset(call.message.chat.id)
    edit_ui_message(call.message, ABOUT_TEXT, kb_back())


@bot.callback_query_handler(func=lambda c: c.data == "menu:new")
def cb_new(call):
    if is_banned(call.message.chat.id):
        bot.answer_callback_query(call.id, "Banned", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    start_flow(call.message.chat.id, edit=call.message)


@bot.callback_query_handler(func=lambda c: c.data == "flow:cancel")
def cb_cancel(call):
    reset(call.message.chat.id)
    bot.answer_callback_query(call.id, "Cancelled")
    edit_ui_message(call.message, "✖️ <b>Cancelled</b>\n\nChoose an option:", kb_main_menu(call.message.chat.id))


@bot.callback_query_handler(func=lambda c: c.data == "flow:rename")
def cb_rename(call):
    chat_id = call.message.chat.id
    if chat_id not in sessions:
        bot.answer_callback_query(call.id, "Session expired.")
        send_home(chat_id, intro=False)
        return
    bot.answer_callback_query(call.id)
    sessions[chat_id]["ui_message"] = call.message
    flow_next(chat_id, progress(0, "New App Name") + "📛 <b>Send the new app name:</b>", step_app_name, True)


@bot.callback_query_handler(func=lambda c: c.data == "flow:build")
def cb_build(call):
    chat_id = call.message.chat.id
    if chat_id not in sessions or "icon" not in sessions[chat_id]:
        bot.answer_callback_query(call.id, "Session expired.")
        send_home(chat_id, intro=False)
        return
    bot.answer_callback_query(call.id, "Build started")
    sessions[chat_id]["ui_message"] = call.message
    flow_screen(chat_id, progress(4, "Building") + "⚙️ <b>Preparing your APK…</b>", source=call.message)
    threading.Thread(target=build_apk, args=(chat_id,), daemon=True).start()


# ==========================================
# 🚀 BUILD FLOW LOGIC
# ==========================================
def start_flow(chat_id, edit=None):
    reset(chat_id)
    sessions[chat_id] = {}
    text = progress(0, "App Name") + "📛 <b>What should your app be called?</b>\n\nSend a name from 1–80 characters."
    flow_next(chat_id, text, step_app_name, source=edit)


def step_app_name(message, rename=False):
    chat_id = message.chat.id
    if chat_id not in sessions:
        return
    name = (message.text or "").strip()
    name_error = validate_app_name(name)
    if name_error:
        flow_next(chat_id, progress(0, "App Name") + f"⚠️ <b>Invalid name</b>\n\n{escape(name_error)}", step_app_name, rename)
        return

    sessions[chat_id]["app_name"] = name
    if rename:
        show_confirmation(chat_id)
        return

    flow_next(chat_id, progress(1, "Website") + f"✅ Name: <b>{safe_name(name)}</b>\n\n📄 <b>Upload website file (.html or .zip):</b>", step_html_file)


def step_html_file(message):
    chat_id = message.chat.id
    if chat_id not in sessions:
        return
    if not message.document:
        flow_next(chat_id, progress(1, "Website") + "⚠️ <b>Please upload a document file (.html or .zip):</b>", step_html_file)
        return
    doc = message.document
    filename = doc.file_name or "index.html"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("html", "htm", "zip"):
        flow_next(chat_id, progress(1, "Website") + "⚠️ <b>Unsupported file format.</b>", step_html_file)
        return
    try:
        info = bot.get_file(doc.file_id)
        content = bot.download_file(info.file_path)
    except Exception as exc:
        flow_next(chat_id, progress(1, "Website") + f"⚠️ <b>Download failed:</b> {escape(str(exc))}", step_html_file)
        return

    sessions[chat_id]["html"] = (filename, content)
    flow_next(chat_id, progress(2, "Icon") + f"✅ Website: <code>{escape(filename)}</code>\n\n🖼️ <b>Upload app icon (PNG/JPEG):</b>", step_icon_file)


def step_icon_file(message):
    chat_id = message.chat.id
    if chat_id not in sessions:
        return
    filename = "icon.png"
    if message.document:
        file_id = message.document.file_id
    elif message.photo:
        file_id = message.photo[-1].file_id
    else:
        flow_next(chat_id, progress(2, "Icon") + "⚠️ <b>Upload a PNG or JPEG photo/document:</b>", step_icon_file)
        return
    try:
        info = bot.get_file(file_id)
        content = bot.download_file(info.file_path)
    except Exception as exc:
        flow_next(chat_id, progress(2, "Icon") + f"⚠️ <b>Download failed:</b> {escape(str(exc))}", step_icon_file)
        return

    icon_error = validate_icon(content)
    if icon_error:
        flow_next(chat_id, progress(2, "Icon") + f"⚠️ <b>Invalid icon:</b> {escape(icon_error)}", step_icon_file)
        return

    sessions[chat_id]["icon"] = (filename, content)
    show_confirmation(chat_id)


def show_confirmation(chat_id):
    session = sessions.get(chat_id)
    if not session:
        return
    html_name, html_bytes = session["html"]
    icon_name, icon_bytes = session["icon"]
    text = (
        progress(3, "Review")
        + "<b>📋 Ready to build</b>\n\n"
        f"📛 <b>Name:</b> {safe_name(session['app_name'])}\n"
        f"📄 <b>Website:</b> <code>{escape(html_name)}</code> ({len(html_bytes):,} bytes)\n"
        f"🖼️ <b>Icon:</b> <code>{escape(icon_name)}</code> ({len(icon_bytes):,} bytes)\n\n"
        "Click <b>Build APK</b> to compile."
    )
    flow_screen(chat_id, text, kb_confirm())


def build_apk(chat_id):
    session = sessions.get(chat_id)
    if not session:
        return
    ui_message = session.get("ui_message")
    stop_event = threading.Event()
    animation_thread = threading.Thread(target=animate_build, args=(ui_message, stop_event), daemon=True) if ui_message else None
    if animation_thread:
        animation_thread.start()

    app_name = session["app_name"]
    html_name, html_bytes = session["html"]
    icon_name, icon_bytes = session["icon"]
    html_mime = "application/zip" if html_name.lower().endswith(".zip") else "text/html"
    icon_mime = "image/png" if icon_name.lower().endswith("png") else "image/jpeg"
    files = {"html": (html_name, html_bytes, html_mime), "icon": (icon_name, icon_bytes, icon_mime)}

    try:
        resp = requests.post(config["api_url"], data={"app_name": app_name}, files=files, timeout=120)
        stop_event.set()
        if animation_thread:
            animation_thread.join(timeout=2)
        content_type = resp.headers.get("Content-Type", "")
        if resp.status_code == 200 and "application/vnd.android.package-archive" in content_type:
            if ui_message:
                edit_ui_message(ui_message, "<b>✅ APK BUILD COMPLETE</b>\n\n📦 Sending APK file…")
            disposition = resp.headers.get("Content-Disposition", "")
            match = re.search(r'filename=(?:"([^"]+)"|([^;]+))', disposition)
            apk_name = (match.group(1) or match.group(2)).strip() if match else f"{app_name}.apk"
            if not apk_name.lower().endswith(".apk"):
                apk_name += ".apk"
            bot.send_document(
                chat_id,
                (apk_name, resp.content),
                caption=f"🎉 <b>Build complete!</b>\n\n✅ <code>{escape(apk_name)}</code> is ready.",
                reply_markup=kb_main_menu(chat_id),
            )
        else:
            msg = f"❌ <b>Build Failed ({resp.status_code})</b>\n\n{api_error_message(resp)}"
            edit_ui_message(ui_message, msg, kb_main_menu(chat_id)) if ui_message else bot.send_message(chat_id, msg, reply_markup=kb_main_menu(chat_id))
    except Exception as exc:
        stop_event.set()
        if animation_thread:
            animation_thread.join(timeout=2)
        msg = f"❌ <b>Error:</b> {escape(str(exc))}"
        edit_ui_message(ui_message, msg, kb_main_menu(chat_id)) if ui_message else bot.send_message(chat_id, msg, reply_markup=kb_main_menu(chat_id))
    finally:
        reset(chat_id)


@bot.message_handler(func=lambda m: True, content_types=["text", "document", "photo"])
def fallback(message):
    chat_id = message.chat.id
    if is_banned(chat_id):
        bot.send_message(chat_id, "🚫 You are banned from using this bot.")
        return
    if chat_id in sessions:
        bot.send_message(chat_id, "⚠️ Please complete the current step or click Cancel.", reply_markup=kb_cancel())
    else:
        bot.send_message(chat_id, "Choose an option from the menu below:", reply_markup=kb_main_menu(chat_id))


if __name__ == "__main__":
    ensure_banner()
    logger.info("Bot started successfully...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)