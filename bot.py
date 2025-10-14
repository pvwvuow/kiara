TOKEN = "8373142020:AAFXndj1zqrO83WViS3kRyjAcXKSXZLDbbE"

import asyncio
import random
import logging
import json
import os
import tempfile
import re
import time
from typing import Optional, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
# <<< تغییر ۱: دیگر نیازی به ایمپورت این موارد در اینجا نیست، چون به ماژول منتقل شدند >>>
# from telegram import InlineQueryResultArticle, InputTextMessageContent 

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    InlineQueryHandler
)

import vocab_data as VOCAB
from messages_lang_fa import LANG as LANG_FA
from messages_lang_ru import LANG as LANG_RU
from messages_lang_ar import LANG as LANG_AR
from messages_lang_hi import LANG as LANG_HI

# Import the game and translator modules
import rps_game
# <<< تغییر ۲: ایمپورت کردن ماژول جدید ترجمه >>>
import translator

LANG_PACKS = {
    "fa": LANG_FA,
    "ru": LANG_RU,
    "ar": LANG_AR,
    "hi": LANG_HI,
}

# Initialize LIBRARIES and STAGES from VOCAB default
LIBRARIES = VOCAB.LIBRARIES
STAGES = VOCAB.STAGES

def refresh_vocab_globals():
    global LIBRARIES, STAGES
    LIBRARIES = VOCAB.LIBRARIES
    STAGES = VOCAB.STAGES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COUNT_FILE = "count.json"
ADMIN_CHAT_ID = 897738216

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DOUBLE_CLICK_THRESHOLD = 0.7
MISTAKES_PER_PAGE = 20
MARKED_PER_PAGE = 20

# -------------------------
# Persistence helpers
def load_user_data():
    try:
        with open(COUNT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def atomic_save(path, data):
    dirpath = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".tmp_count_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

def save_user_data(data):
    atomic_save(COUNT_FILE, data)

def get_user_profile(chat_id):
    data = load_user_data()
    return data.get(str(chat_id), {
        "chat_id": str(chat_id),
        "user_name": None,
        "score": 0.0,
        "completed_levels": [],
        "mistake_words": [],
        "mistake_counts": {},
        "last_active": None,
        "daily_reminder": None,
        "marked_words": [],
        "lang": None
    })

def update_user_profile(chat_id, updates):
    data = load_user_data()
    user = data.get(str(chat_id), {
        "chat_id": str(chat_id),
        "user_name": None,
        "score": 0.0,
        "completed_levels": [],
        "mistake_words": [],
        "mistake_counts": {},
        "last_active": None,
        "daily_reminder": None,
        "marked_words": [],
        "lang": None
    })
    user.update(updates)
    data[str(chat_id)] = user
    save_user_data(data)

def record_quiz_result(chat_id, completed_level_increment, completed_level, mistakes):
    profile = get_user_profile(chat_id)
    try:
        if completed_level_increment and int(completed_level_increment) != 0:
            profile["score"] = int(profile.get("score", 0)) + int(completed_level_increment)
    except Exception:
        profile["score"] = int(profile.get("score", 0))
    if completed_level:
        if completed_level not in profile.get("completed_levels", []):
            profile.setdefault("completed_levels", []).append(completed_level)
    profile.setdefault("mistake_words", [])
    profile.setdefault("mistake_counts", {})
    for w in mistakes:
        if w not in profile["mistake_words"]:
            profile["mistake_words"].append(w)
        profile["mistake_counts"][w] = profile["mistake_counts"].get(w, 0) + 1
    update_user_profile(chat_id, profile)

def remove_corrected_mistakes(chat_id, corrected_words):
    if not corrected_words:
        return
    profile = get_user_profile(chat_id)
    changed = False
    for w in corrected_words:
        if w in profile.get("mistake_words", []):
            profile["mistake_words"].remove(w)
            profile["mistake_counts"].pop(w, None)
            changed = True
    if changed:
        update_user_profile(chat_id, profile)

def get_total_users_count():
    data = load_user_data()
    return len(data.keys())

def get_top_users(limit=10):
    data = load_user_data()
    users = []
    for k, v in data.items():
        name = v.get("user_name") or k
        score = v.get("score", 0)
        users.append((name, score))
    users.sort(key=lambda x: x[1], reverse=True)
    return users[:limit]

# -------------------------
# i18n helper
def get_user_lang_from_profile(chat_id):
    try:
        profile = get_user_profile(chat_id)
        return profile.get("lang")
    except Exception:
        return None

def set_user_lang_in_profile(chat_id, lang_code):
    profile = get_user_profile(chat_id)
    profile["lang"] = lang_code
    update_user_profile(chat_id, profile)

def MSG(key: str, chat_id: Optional[int], context) -> str:
    lang = None
    try:
        if chat_id:
            lang = get_user_lang_from_profile(chat_id)
        elif context and context.user_data:
            lang = context.user_data.get("lang")
    except Exception:
        lang = "fa"
    pack = LANG_PACKS.get(lang or "fa", LANG_PACKS["fa"])
    return pack.get(key, LANG_PACKS["fa"].get(key, key))

# -------------------------
# Safe messaging helpers
async def safe_send_message(bot, chat_id, text=None, **kwargs):
    if not chat_id or text is None or text == "":
        logger.warning("safe_send_message: missing chat_id or empty text")
        return None
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception:
        logger.exception("safe_send_message failed")
        return None

async def safe_edit_message(bot, chat_id, message_id, **kwargs):
    if not chat_id or not message_id:
        logger.warning("safe_edit_message: missing chat_id/message_id")
        return None
    try:
        return await bot.edit_message_text(chat_id=chat_id, message_id=message_id, **kwargs)
    except Exception:
        # logger.exception("safe_edit_message failed") # Can be noisy, disable if not needed
        return None

async def safe_delete_message(bot, chat_id, message_id):
    try:
        if chat_id and message_id:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

def chat_id_from(update: Optional[Update], context) -> Optional[int]:
    if update and getattr(update, "effective_chat", None):
        return update.effective_chat.id
    if update and getattr(update, "callback_query", None) and update.callback_query.message:
        return update.callback_query.message.chat.id
    if context and context.user_data:
        return context.user_data.get("chat_id")
    return None

# -------------------------
def clamp_lives(lives: int) -> int:
    return max(0, min(3, int(lives)))

def build_header_text(lives: int, current: int, total: int, score: int, chat_id=None, context=None):
    lives = clamp_lives(lives)
    hearts = "❤️" * lives + "💔" * (3 - lives)
    q_of = MSG("question_of", chat_id, context).format(current=current, total=total)
    score_lbl = MSG("score_label", chat_id, context).format(score=score)
    return f"{hearts}\n\n📊 {q_of}    ⭐ {score_lbl}"

# -------------------------
# Added helper: update_header
async def update_header(context, chat_id, current, total, score):
    """
    Update or create the header message that shows lives / progress / score.
    Stores message id in context.user_data['header_msg_id'].
    """
    try:
        header_text = build_header_text(context.user_data.get("lives", 3), current, total, score, chat_id, context)
        header_id = context.user_data.get("header_msg_id")
        if header_id:
            try:
                await safe_edit_message(context.bot, chat_id, header_id, text=header_text)
                return
            except Exception:
                context.user_data.pop("header_msg_id", None)
        msg = await safe_send_message(context.bot, chat_id, text=header_text)
        if msg:
            context.user_data["header_msg_id"] = msg.message_id
    except Exception:
        logger.exception("update_header failed for chat %s", chat_id)

# -------------------------
# Quiz and UI helpers
def label_with_star(word, chat_id):
    profile = get_user_profile(chat_id)
    marked = profile.get("marked_words", [])
    return f"{word} 🌟" if word in marked else word

def generate_quiz_questions(words_dict):
    questions = []
    items = list(words_dict.items())
    for word, correct in items:
        options = [correct]
        while len(options) < 4:
            _, distractor = random.choice(items)
            if distractor not in options:
                options.append(distractor)
        random.shuffle(options)
        questions.append({"word": word, "options": options, "answer": correct})
    return questions

async def schedule_translation(context, chat_id, query, word, translation, delay):
    try:
        await asyncio.sleep(delay)
        last = context.user_data.get("last_cb", {})
        expected = f"word_{word}" if getattr(query, "data", "").startswith("word_") else getattr(query, "data", "")
        if last.get("data") == expected:
            try:
                await query.answer(text=f"{word} → {translation}", show_alert=True)
            except Exception:
                await safe_send_message(context.bot, chat_id, text=f"{word} → {translation}")
        context.user_data.pop("last_cb_task", None)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("schedule_translation failed for %s", word)
        context.user_data.pop("last_cb_task", None)

def cancel_scheduled_translation(context):
    task = context.user_data.pop("last_cb_task", None)
    if task and not task.done():
        try:
            task.cancel()
        except Exception:
            pass

# -------------------------
# Handlers (start + language selection)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    context.user_data["chat_id"] = chat_id
    context.user_data.setdefault("selected_stage", None)
    context.user_data.setdefault("selected_library", None)
    context.user_data["quiz_cancelled"] = False
    context.user_data.setdefault("review_words", [])
    context.user_data.setdefault("mistake_words", [])
    context.user_data.setdefault("mistake_counts", {})
    context.user_data.setdefault("feedback_msgs", [])
    context.user_data.setdefault("mistake_quiz_mode", False)
    context.user_data.setdefault("mistake_quiz_corrected", [])
    context.user_data.setdefault("completed_levels", [])
    context.user_data.setdefault("marked_words", [])
    context.user_data.setdefault("lock", asyncio.Lock())
    context.user_data.setdefault("daily_task", None)
    context.user_data.setdefault("last_cb", {"data": None, "ts": 0.0})

    user = update.effective_user
    name = (user.full_name if user and getattr(user, "full_name", None) else
            (user.first_name if user and getattr(user, "first_name", None) else MSG("default_username", chat_id, context)))
    context.user_data["user_name"] = name

    profile = get_user_profile(chat_id)
    if profile.get("user_name") != name:
        profile["user_name"] = name
        update_user_profile(chat_id, profile)

    data = load_user_data()
    is_new_user = str(chat_id) not in data

    if is_new_user or not profile.get("lang"):
        keyboard = [
            [InlineKeyboardButton("فارسی 🇮🇷", callback_data="set_lang_fa"),
             InlineKeyboardButton("Русский 🇷🇺", callback_data="set_lang_ru")],
            [InlineKeyboardButton("العربية 🇸🇦", callback_data="set_lang_ar")],
            [InlineKeyboardButton("हिन्दी 🇮🇳", callback_data="set_lang_hi")]
        ]
        reply = InlineKeyboardMarkup(keyboard)
        await safe_send_message(context.bot, chat_id, text=MSG("choose_lang", chat_id, context), reply_markup=reply)
        return
    
    # CHANGE 3: "Play with Friends" button replaces "Rock, Paper, Scissors"
    keyboard = [
        [KeyboardButton(MSG("start_learning_btn", chat_id, context)), KeyboardButton("⚙️ " + MSG("settings_btn", chat_id, context))],
        [KeyboardButton("👤 " + MSG("profile_btn", chat_id, context)), KeyboardButton(MSG("play_with_friends_btn", chat_id, context))],
        [KeyboardButton("🏆 " + MSG("leaderboard_btn", chat_id, context)), KeyboardButton("🌐 " + MSG("change_lang_btn", chat_id, context))]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await safe_send_message(context.bot, chat_id, text=MSG("welcome", chat_id, context))
    await safe_send_message(context.bot, chat_id, text=MSG("main_menu_title", chat_id, context), reply_markup=reply_markup)

async def handle_set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id if query.message else context.user_data.get("chat_id")
    lang_code = None
    if data == "set_lang_fa":
        lang_code = "fa"
        set_user_lang_in_profile(chat_id, "fa")
    elif data == "set_lang_ru":
        lang_code = "ru"
        set_user_lang_in_profile(chat_id, "ru")
    elif data == "set_lang_hi":
        lang_code = "hi"
        set_user_lang_in_profile(chat_id, "hi")
    elif data == "set_lang_ar":
        lang_code = "ar"
        set_user_lang_in_profile(chat_id, "ar")
    if lang_code:
        try:
            VOCAB.set_default_lang(lang_code)
            refresh_vocab_globals()
        except Exception:
            logger.exception("Failed to switch VOCAB default lang to %s", lang_code)
    try:
        await safe_edit_message(context.bot, chat_id, query.message.message_id, text=MSG("lang_set_ok", chat_id, context))
    except Exception:
        await safe_send_message(context.bot, chat_id, text=MSG("lang_set_ok", chat_id, context))
    await start(update, context)

# -------------------------
# Menus and callbacks (all strings use MSG)
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    keyboard = [
        [KeyboardButton("📘 " + MSG("learn_new_btn", chat_id, context)), KeyboardButton("🔁 " + MSG("review_btn", chat_id, context))],
        [KeyboardButton("❌ " + MSG("my_mistakes_btn", chat_id, context)), KeyboardButton("📚 " + MSG("markbook_btn", chat_id, context))],
        [KeyboardButton("⏰ " + MSG("daily_reminder_btn", chat_id, context))],
        [KeyboardButton("🔙 " + MSG("back_btn", chat_id, context))]
    ]
    if chat_id == ADMIN_CHAT_ID:
        keyboard.insert(2, [KeyboardButton("📊 " + MSG("admin_stats_btn", chat_id, context))])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await safe_send_message(context.bot, chat_id, text=MSG("main_menu_title", chat_id, context), reply_markup=reply_markup)

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    keyboard = [[KeyboardButton("👤 " + MSG("profile_btn", chat_id, context)), KeyboardButton("🌐 " + MSG("change_lang_btn", chat_id, context))],[KeyboardButton("🔙 " + MSG("back_btn", chat_id, context))]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await safe_send_message(context.bot, chat_id, text=MSG("settings_title", chat_id, context), reply_markup=reply_markup)

async def handle_main_menu_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.user_data["chat_id"] = chat_id
    
    if context.user_data.get("awaiting_reminder_time"):
        handled = await handle_time_message_for_reminder(update, context)
        if handled:
            return

    text = update.message.text.strip()
    
    # CHANGE 4: All ReplyKeyboard buttons are now handled here for proper i18n
    if text == MSG("start_learning_btn", chat_id, context):
        await show_main_menu(update, context)
    elif text == "⚙️ " + MSG("settings_btn", chat_id, context):
        await show_settings_menu(update, context)
    elif text == MSG("play_with_friends_btn", chat_id, context):
        bot_username = (await context.bot.get_me()).username
        instructions = MSG("play_with_friends_instructions", chat_id, context).format(bot_username=bot_username)
        await update.message.reply_text(instructions)
    elif text == "🔙 " + MSG("back_btn", chat_id, context):
        await start(update, context)
    elif text == "📘 " + MSG("learn_new_btn", chat_id, context):
        await show_stage_list(update, context)
    elif text == "🔁 " + MSG("review_btn", chat_id, context):
        await handle_review_menu(update, context)
    elif text == "❌ " + MSG("my_mistakes_btn", chat_id, context):
        await show_mistake_menu(update, context)
    elif text == "📚 " + MSG("markbook_btn", chat_id, context):
        await show_marked_menu(update, context)
    elif text == "👤 " + MSG("profile_btn", chat_id, context):
        await show_profile(update, context)
    elif text == "🌐 " + MSG("change_lang_btn", chat_id, context):
        await safe_send_message(context.bot, chat_id, text=MSG("choose_lang", chat_id, context), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("فارسی 🇮🇷", callback_data="set_lang_fa"),
             InlineKeyboardButton("Русский 🇷🇺", callback_data="set_lang_ru")],
            [InlineKeyboardButton("العربية 🇸🇦", callback_data="set_lang_ar")],
            [InlineKeyboardButton("हिन्दी 🇮🇳", callback_data="set_lang_hi")]
        ]))
    elif text == "🏆 " + MSG("leaderboard_btn", chat_id, context):
        await show_leaderboard(update, context)
    elif text == "📊 " + MSG("admin_stats_btn", chat_id, context):
        if chat_id == ADMIN_CHAT_ID:
            total = get_total_users_count()
            await update.message.reply_text(MSG("admin_total_users", chat_id, context).format(total=total))
        else:
            await update.message.reply_text(MSG("admin_not_allowed", chat_id, context))
    elif text == "⏰ " + MSG("daily_reminder_btn", chat_id, context):
        await start_daily_reminder_flow(update, context)
    else:
        await update.message.reply_text(MSG("invalid_option", chat_id, context))

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    top = get_top_users(limit=10)
    if not top:
        await safe_send_message(context.bot, chat_id, text=MSG("no_users", chat_id, context))
        return
    lines = [f"{i+1}. {name} — {int(score) if float(score).is_integer() else score}" for i, (name, score) in enumerate(top)]
    text = MSG("leaderboard_title", chat_id, context) + "\n" + "\n".join(lines)
    await safe_send_message(context.bot, chat_id, text=text)

async def handle_review_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    review_words = context.user_data.get("review_words", [])
    if not review_words:
        await safe_send_message(context.bot, chat_id, MSG("no_review_words", chat_id, context))
        return
    await safe_send_message(context.bot, chat_id, MSG("review_list_title", chat_id, context) + "\n" + "\n".join(review_words))

async def show_stage_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    
    # CHANGE 1: Two-column layout for stages
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    
    stage_items = list(STAGES.items())
    for key, meta in stage_items:
        row.append(InlineKeyboardButton(meta["title"], callback_data=key))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row: # Add the last row if it's not full
        buttons.append(row)
        
    buttons.append([InlineKeyboardButton(MSG("back_btn", chat_id, context), callback_data="back_to_main")])
    reply = InlineKeyboardMarkup(buttons)
    
    text_to_send = MSG("choose_stage", chat_id, context)
    
    if getattr(update, "callback_query", None):
        query = update.callback_query
        await query.answer()
        await safe_edit_message(context.bot, query.message.chat.id, query.message.message_id, text=text_to_send, reply_markup=reply)
    else:
        await safe_send_message(context.bot, chat_id, text=text_to_send, reply_markup=reply)


async def handle_stage_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    stage_meta = STAGES.get(data)
    if not stage_meta:
        await query.answer(text=MSG("stage_not_found", query.message.chat.id, context), show_alert=True)
        return
    context.user_data["selected_stage"] = data
    rows = []
    profile = get_user_profile(query.message.chat.id)
    completed = profile.get("completed_levels", [])
    for lib_key in stage_meta["libraries"]:
        lib = LIBRARIES.get(lib_key)
        title = lib["title"] if lib else lib_key
        display = f"✔️ {title}" if lib_key in completed else title
        rows.append([InlineKeyboardButton(display, callback_data=lib_key)])
    rows.append([InlineKeyboardButton(MSG("back_btn", query.message.chat.id, context), callback_data="back_to_stages")])
    reply = InlineKeyboardMarkup(rows)
    try:
        await safe_edit_message(context.bot, query.message.chat.id, query.message.message_id, text=MSG("choose_level", query.message.chat.id, context).format(stage=stage_meta["title"]), reply_markup=reply)
    except Exception:
        await safe_send_message(context.bot, query.message.chat.id, text=MSG("choose_level", query.message.chat.id, context).format(stage=stage_meta["title"]), reply_markup=reply)

async def handle_back_to_stages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_stage_list(update, context)

async def handle_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if getattr(update, "callback_query", None):
        query = update.callback_query
        try: await query.answer()
        except Exception: pass
        try: await safe_delete_message(context.bot, query.message.chat.id, query.message.message_id)
        except Exception: pass

    await start(update, context)

async def show_words_then_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    context.user_data["chat_id"] = chat_id
    lib_key = query.data
    library = LIBRARIES.get(lib_key)
    if not library:
        await safe_edit_message(context.bot, chat_id, query.message.message_id, text=MSG("library_not_found", chat_id, context))
        return
    words = library["words"]
    context.user_data["selected_library"] = lib_key
    context.user_data["quiz_cancelled"] = False
    buttons = []
    row = []
    for word in words.keys():
        label = label_with_star(word, chat_id)
        row.append(InlineKeyboardButton(label, callback_data=f"word_{word}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
        
    # CHANGE 2: "Smart" back button - points back to the parent stage level list
    parent_stage = context.user_data.get("selected_stage")
    back_callback = f"stage_{parent_stage}" if parent_stage else "back_to_stages"
    
    buttons.append([InlineKeyboardButton(MSG("start_quiz_btn", chat_id, context), callback_data="start_quiz"),
                    InlineKeyboardButton(MSG("back_btn", chat_id, context), callback_data=back_callback)])
    await safe_edit_message(context.bot, chat_id, query.message.message_id,
                            text=MSG("library_instructions", chat_id, context).format(title=library.get("title", lib_key)),
                            reply_markup=InlineKeyboardMarkup(buttons))

# ... (The rest of the file remains the same until the handler registration part) ...

# [... The rest of your code from show_library_from_reminder down to global_error_handler is unchanged ...]
# [I'm omitting it here for brevity, but it should be kept in your file.]

async def show_library_from_reminder(query, context, lib_key):
    chat_id = query.message.chat.id if query and getattr(query, "message", None) else context.user_data.get("chat_id")
    library = LIBRARIES.get(lib_key)
    if not library:
        try:
            if getattr(query, "message", None):
                await safe_edit_message(context.bot, chat_id, query.message.message_id, text=MSG("library_not_found", chat_id, context))
            else:
                await safe_send_message(context.bot, chat_id, text=MSG("library_not_found", chat_id, context))
        except Exception:
            pass
        return

    context.user_data["chat_id"] = chat_id
    context.user_data["selected_library"] = lib_key
    context.user_data["quiz_cancelled"] = False

    words = library.get("words", {})
    buttons = []
    row = []
    for word in words.keys():
        label = label_with_star(word, chat_id)
        row.append(InlineKeyboardButton(label, callback_data=f"word_{word}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(MSG("start_quiz_btn", chat_id, context), callback_data="start_quiz"),
                    InlineKeyboardButton(MSG("back_btn", chat_id, context), callback_data="back_to_stages")])

    text = MSG("library_instructions", chat_id, context).format(title=library.get("title", lib_key))
    try:
        if getattr(query, "message", None):
            await safe_edit_message(context.bot, chat_id, query.message.message_id, text=text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await safe_send_message(context.bot, chat_id, text=text, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        try:
            await safe_send_message(context.bot, chat_id, text=text, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            pass

async def handle_word_popup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data
    if not data.startswith("word_"):
        try:
            await query.answer()
        except Exception:
            pass
        return

    chat_id = query.message.chat.id if query.message else context.user_data.get("chat_id")
    context.user_data.setdefault("last_cb", {"data": None, "ts": 0.0})
    now_ts = time.time()
    last = context.user_data.get("last_cb", {"data": None, "ts": 0.0})
    last_data = last.get("data")
    last_ts = last.get("ts", 0.0)
    is_double = (last_data == data) and (now_ts - last_ts <= DOUBLE_CLICK_THRESHOLD)

    word = data.replace("word_", "")

    if is_double:
        cancel_scheduled_translation(context)
        profile = get_user_profile(chat_id)
        marked = profile.get("marked_words", [])
        if word in marked:
            marked.remove(word)
            msg = MSG("unmarked_msg", chat_id, context).format(word=word)
        else:
            marked.append(word)
            msg = MSG("marked_msg", chat_id, context).format(word=word)
        profile["marked_words"] = marked
        update_user_profile(chat_id, profile)
        context.user_data["last_cb"] = {"data": None, "ts": 0.0}
        try:
            await query.answer(text=msg, show_alert=False)
        except Exception:
            await safe_send_message(context.bot, chat_id, text=msg)
        try:
            msg_obj = query.message
            if msg_obj and msg_obj.reply_markup:
                lib_key = context.user_data.get("selected_library")
                if lib_key:
                    library = LIBRARIES.get(lib_key)
                    if library:
                        words = library["words"]
                        new_buttons = []
                        row = []
                        for w in words.keys():
                            label = label_with_star(w, chat_id)
                            row.append(InlineKeyboardButton(label, callback_data=f"word_{w}"))
                            if len(row) == 2:
                                new_buttons.append(row)
                                row = []
                        if row:
                            new_buttons.append(row)
                        
                        parent_stage = context.user_data.get("selected_stage")
                        back_callback = f"stage_{parent_stage}" if parent_stage else "back_to_stages"
                        
                        new_buttons.append([InlineKeyboardButton(MSG("start_quiz_btn", chat_id, context), callback_data="start_quiz"),
                                            InlineKeyboardButton(MSG("back_btn", chat_id, context), callback_data=back_callback)])
                        await safe_edit_message(context.bot, query.message.chat.id, msg_obj.message_id, text=msg_obj.text, reply_markup=InlineKeyboardMarkup(new_buttons))
        except Exception:
            pass
        return
    else:
        context.user_data["last_cb"] = {"data": data, "ts": now_ts}
        cancel_scheduled_translation(context)
        translation = None
        lib_key = context.user_data.get("selected_library")
        if lib_key:
            lib = LIBRARIES.get(lib_key)
            if lib:
                translation = lib["words"].get(word)
        if not translation:
            for lib in LIBRARIES.values():
                if word in lib.get("words", {}):
                    translation = lib["words"][word]
                    break
        translation = translation or MSG("translation_not_found", chat_id, context)
        task = asyncio.create_task(schedule_translation(context, chat_id, query, word, translation, DOUBLE_CLICK_THRESHOLD))
        context.user_data["last_cb_task"] = task
        return

async def handle_quiz_start_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    context.user_data["chat_id"] = chat_id
    lib_key = context.user_data.get("selected_library")
    library = LIBRARIES.get(lib_key)
    if not library:
        await safe_edit_message(context.bot, chat_id, query.message.message_id, text=MSG("library_not_found", chat_id, context))
        return

    context.user_data.setdefault("lock", asyncio.Lock())
    lock = context.user_data["lock"]

    async with lock:
        context.user_data["lives"] = 3
        context.user_data.pop("header_msg_id", None)

        questions = generate_quiz_questions(library["words"])
        context.user_data["quiz"] = {"questions": questions, "current": 0, "score": 0, "mistakes": []}
        context.user_data["mistake_quiz_mode"] = False
        context.user_data["mistake_quiz_corrected"] = []
        context.user_data["feedback_msgs"] = []
        context.user_data["quiz_cancelled"] = False
        context.user_data.pop("question_timer_task", None)
        context.user_data.pop("sending_question", None)

        header_text = build_header_text(context.user_data["lives"], 0, len(questions), 0, chat_id, context)
        try:
            header_msg = await safe_send_message(context.bot, chat_id, text=header_text)
            if header_msg:
                context.user_data["header_msg_id"] = header_msg.message_id
        except Exception:
            context.user_data.pop("header_msg_id", None)

    await send_quiz_question(update, context)

async def send_quiz_question(update: Optional[Update], context: ContextTypes.DEFAULT_TYPE):
    lock = context.user_data.setdefault("lock", asyncio.Lock())
    async with lock:
        if context.user_data.get("sending_question"):
            return
        context.user_data["sending_question"] = True
        try:
            chat_id = chat_id_from(update, context)
            if context.user_data.get("quiz_cancelled"):
                return
            if not chat_id:
                context.user_data["quiz_cancelled"] = True
                context.user_data["quiz"] = None
                return
            quiz = context.user_data.get("quiz")
            if not quiz:
                return
            if "lives" not in context.user_data:
                context.user_data["lives"] = 3
            await update_header(context, chat_id, quiz.get("current", 0), len(quiz.get("questions", [])), quiz.get("score", 0))
            if context.user_data.get("lives", 3) <= 0:
                context.user_data["quiz_cancelled"] = True
                context.user_data["quiz"] = None
                for msg_id in context.user_data.get("feedback_msgs", []):
                    await safe_delete_message(context.bot, chat_id, msg_id)
                context.user_data["feedback_msgs"] = []
                header_id = context.user_data.pop("header_msg_id", None)
                final_text = MSG("lost_three_lives", chat_id, context) + "\n\n" + build_header_text(0, 0, len(quiz.get("questions", [])), quiz.get("score", 0), chat_id, context)
                if header_id:
                    try:
                        await safe_edit_message(context.bot, chat_id, header_id, text=final_text)
                    except Exception:
                        await safe_send_message(context.bot, chat_id, text=final_text)
                else:
                    await safe_send_message(context.bot, chat_id, text=final_text)
                return
            current = quiz.get("current", 0)
            questions = quiz.get("questions", [])
            if current >= len(questions):
                score = quiz.get("score", 0)
                total = len(questions)
                user_mistakes = context.user_data.get("mistake_words", [])
                for w in quiz.get("mistakes", []):
                    if w not in user_mistakes:
                        user_mistakes.append(w)
                context.user_data["mistake_words"] = user_mistakes
                lib_key = context.user_data.get("selected_library")
                if not context.user_data.get("mistake_quiz_mode") and lib_key and (not quiz.get("mistakes")) and score == total:
                    completed = context.user_data.get("completed_levels", [])
                    if lib_key not in completed:
                        completed.append(lib_key)
                        context.user_data["completed_levels"] = completed
                    try:
                        record_quiz_result(chat_id, 1, lib_key, [])
                    except Exception:
                        logger.exception("Failed to record perfect quiz result for user %s", chat_id)
                else:
                    try:
                        if quiz.get("mistakes"):
                            record_quiz_result(chat_id, 0, None, quiz.get("mistakes", []))
                    except Exception:
                        logger.exception("Failed to record partial quiz mistakes for user %s", chat_id)
                header_id = context.user_data.pop("header_msg_id", None)
                if header_id:
                    try:
                        final_text = MSG("quiz_finished", chat_id, context) + "\n\n" + build_header_text(context.user_data.get("lives", 0), len(questions), len(questions), score, chat_id, context)
                        await safe_edit_message(context.bot, chat_id, header_id, text=final_text)
                    except Exception:
                        pass
                    try:
                        await asyncio.sleep(5)
                        await safe_delete_message(context.bot, chat_id, header_id)
                    except Exception:
                        pass
                context.user_data.pop("lives", None)
                context.user_data.pop("quiz_cancelled", None)
                for msg_id in context.user_data.get("feedback_msgs", []):
                    await safe_delete_message(context.bot, chat_id, msg_id)
                context.user_data["feedback_msgs"] = []
                await safe_send_message(context.bot, chat_id, text=MSG("quiz_finished_score", chat_id, context).format(score=score, total=total))
                return
            prev_qid = context.user_data.get("question_msg_id")
            if prev_qid:
                try:
                    await safe_delete_message(context.bot, chat_id, prev_qid)
                except Exception:
                    pass
                context.user_data.pop("question_msg_id", None)
            q = questions[current]
            context.user_data["current_word"] = q["word"]
            context.user_data["current_options"] = q["options"]
            context.user_data["answered"] = False
            keyboard = [[InlineKeyboardButton(opt, callback_data=opt)] for opt in q["options"]]
            keyboard.append([InlineKeyboardButton(MSG("cancel_quiz_btn", chat_id, context), callback_data="cancel_quiz")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            question_text = (
                MSG("question_prompt", chat_id, context).format(word=q['word']) + "\n"
                + MSG("time_remaining", chat_id, context).format(seconds=10) + "\n"
                + MSG("question_count", chat_id, context).format(current=current + 1, total=len(questions))
            )
            msg = await safe_send_message(context.bot, chat_id, text=question_text, reply_markup=reply_markup)
            if not msg:
                context.user_data["quiz_cancelled"] = True
                context.user_data["quiz"] = None
                return
            context.user_data["question_msg_id"] = msg.message_id
            task = asyncio.create_task(update_timer_in_question(context, chat_id, msg.message_id, q["word"]))
            context.user_data["question_timer_task"] = task
            await update_header(context, chat_id, current + 1, len(questions), quiz.get("score", 0))
        finally:
            context.user_data["sending_question"] = False

async def update_timer_in_question(context: ContextTypes.DEFAULT_TYPE, chat_id, message_id, word):
    if not chat_id:
        return
    try:
        for i in range(9, 0, -1):
            await asyncio.sleep(1)
            if context.user_data.get("quiz_cancelled"):
                return
            quiz = context.user_data.get("quiz")
            current_word = context.user_data.get("current_word")
            if context.user_data.get("answered") or current_word != word:
                return
            try:
                if context.user_data.get("quiz_cancelled"):
                    return
                await safe_edit_message(context.bot, chat_id, message_id,
                                        text=(MSG("question_prompt", chat_id, context).format(word=word) + "\n" + MSG("seconds_left", chat_id, context).format(seconds=i) + "\n" + MSG("question_count", chat_id, context).format(current=quiz['current'] + 1, total=len(quiz['questions']))),
                                        reply_markup=InlineKeyboardMarkup([*[[InlineKeyboardButton(opt, callback_data=opt)] for opt in quiz["questions"][quiz["current"]]["options"]],[InlineKeyboardButton(MSG("cancel_quiz_btn", chat_id, context), callback_data="cancel_quiz")]]))
            except Exception:
                break
        if not context.user_data.get("answered") and not context.user_data.get("quiz_cancelled"):
            quiz = context.user_data.get("quiz") or {"mistakes": [], "current": 0, "questions": []}
            quiz.setdefault("mistakes", []).append(word)
            context.user_data["lives"] = max(0, context.user_data.get("lives", 3) - 1)
            await update_header(context, chat_id, quiz.get("current", 0) + 1, len(quiz.get("questions", [])), quiz.get("score", 0))
            quiz["current"] = quiz.get("current", 0) + 1
            context.user_data["quiz"] = quiz
            try:
                await safe_edit_message(context.bot, chat_id, message_id,
                                        text=MSG("time_up", chat_id, context).format(word=word, current=quiz['current'], total=len(quiz.get("questions", []))))
            except Exception:
                pass
            if context.user_data.get("lives", 3) <= 0:
                try:
                    if quiz.get("mistakes"):
                        record_quiz_result(chat_id, 0, None, quiz.get("mistakes", []))
                except Exception:
                    logger.exception("Failed to record final mistakes for user %s", chat_id)
                context.user_data["quiz_cancelled"] = True
                context.user_data["quiz"] = None
                q_msg_id = context.user_data.get("question_msg_id")
                await safe_delete_message(context.bot, chat_id, q_msg_id)
                header_id = context.user_data.pop("header_msg_id", None)
                final_text = MSG("lost_three_lives", chat_id, context) + "\n\n" + build_header_text(0, quiz.get("current",0), len(quiz.get("questions",[])), quiz.get("score",0), chat_id, context)
                if header_id:
                    try:
                        await safe_edit_message(context.bot, chat_id, header_id, text=final_text)
                    except Exception:
                        await safe_send_message(context.bot, chat_id, text=final_text)
                for msg_id in context.user_data.get("feedback_msgs", []):
                    await safe_delete_message(context.bot, chat_id, msg_id)
                context.user_data["feedback_msgs"] = []
                return
            await asyncio.sleep(1)
            if not context.user_data.get("quiz_cancelled"):
                await send_quiz_question(None, context)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Exception in update_timer_in_question")

async def cancel_and_await_task(task):
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

async def handle_quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return
    data = update.callback_query.data
    if data in ("cancel_quiz", "back_to_main", "back_to_stages") or data.startswith("word_") or data == "view_top_mistakes":
        return
    if context.user_data.get("quiz_cancelled"):
        return
    query = update.callback_query
    await query.answer()
    selected = query.data
    quiz = context.user_data.get("quiz")
    if not quiz:
        return
    options = context.user_data.get("current_options", [])
    if selected not in options:
        try:
            await safe_edit_message(context.bot, query.message.chat.id, query.message.message_id, text=MSG("invalid_choice", query.message.chat.id, context))
        except Exception:
            pass
        return

    lock = context.user_data.setdefault("lock", asyncio.Lock())
    async with lock:
        task = context.user_data.pop("question_timer_task", None)
        if task:
            try:
                await cancel_and_await_task(task)
            except Exception:
                pass

        current = quiz.get("current", 0)
        questions = quiz.get("questions", [])
        if current >= len(questions):
            return
        question = questions[current]
        correct = question["answer"]
        word = question["word"]
        context.user_data["answered"] = True
        if selected == correct:
            quiz["score"] = quiz.get("score", 0) + 1
            result = MSG("correct_answer", query.message.chat.id, context)
            if context.user_data.get("mistake_quiz_mode"):
                corrected = context.user_data.setdefault("mistake_quiz_corrected", [])
                if word not in corrected:
                    corrected.append(word)
        else:
            quiz.setdefault("mistakes", []).append(word)
            result = MSG("wrong_answer", query.message.chat.id, context).format(correct=correct)
            context.user_data["lives"] = max(0, context.user_data.get("lives", 3) - 1)
            mistakes = context.user_data.get("mistake_words", [])
            if word not in mistakes:
                mistakes.append(word)
                context.user_data["mistake_words"] = mistakes
            counts = context.user_data.get("mistake_counts", {})
            counts[word] = counts.get(word, 0) + 1
            context.user_data["mistake_counts"] = counts

            await update_header(context, query.message.chat.id, quiz.get("current",0)+1, len(quiz.get("questions",[])), quiz.get("score",0))
            if context.user_data.get("lives", 3) <= 0:
                try:
                    if quiz.get("mistakes"):
                        record_quiz_result(query.message.chat.id, 0, None, quiz.get("mistakes", []))
                except Exception:
                    logger.exception("Failed to record final mistakes for user %s", query.message.chat.id)
                context.user_data["quiz_cancelled"] = True
                context.user_data["quiz"] = None
                q_msg_id = context.user_data.get("question_msg_id")
                await safe_delete_message(context.bot, query.message.chat.id, q_msg_id)
                for msg_id in context.user_data.get("feedback_msgs", []):
                    await safe_delete_message(context.bot, query.message.chat.id, msg_id)
                context.user_data["feedback_msgs"] = []
                header_id = context.user_data.pop("header_msg_id", None)
                final_text = MSG("lost_three_lives", query.message.chat.id, context) + "\n\n" + build_header_text(0, quiz.get("current",0), len(quiz.get("questions",[])), quiz.get("score",0), query.message.chat.id, context)
                if header_id:
                    try:
                        await safe_edit_message(context.bot, query.message.chat.id, header_id, text=final_text)
                    except Exception:
                        await safe_send_message(context.bot, query.message.chat.id, text=final_text)
                return

        quiz["current"] = current + 1
        context.user_data["quiz"] = quiz
        try:
            msg = await safe_edit_message(context.bot, query.message.chat.id, query.message.message_id, text=result)
            if msg:
                feedback_msgs = context.user_data.get("feedback_msgs", [])
                feedback_msgs.append(msg.message_id)
                context.user_data["feedback_msgs"] = feedback_msgs
        except Exception:
            pass

    await update_header(context, query.message.chat.id, quiz.get("current",0), len(quiz.get("questions",[])), quiz.get("score",0))
    await asyncio.sleep(1)
    if not context.user_data.get("quiz_cancelled"):
        context.user_data["answered"] = False
        await send_quiz_question(None, context)

async def handle_cancel_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = chat_id_from(update, context)
    context.user_data["chat_id"] = chat_id

    lock = context.user_data.setdefault("lock", asyncio.Lock())
    async with lock:
        context.user_data["quiz_cancelled"] = True
        task = context.user_data.pop("question_timer_task", None)
        if task:
            try:
                await cancel_and_await_task(task)
            except Exception:
                pass
        if context.user_data.get("mistake_quiz_mode"):
            corrected = context.user_data.get("mistake_quiz_corrected", [])
            if corrected:
                mistakes = context.user_data.get("mistake_words", [])
                for w in corrected:
                    if w in mistakes:
                        mistakes.remove(w)
                context.user_data["mistake_words"] = mistakes
                try:
                    remove_corrected_mistakes(chat_id, corrected)
                except Exception:
                    logger.exception("Failed to remove corrected mistakes for user %s", chat_id)
            context.user_data["mistake_quiz_mode"] = False
            context.user_data["mistake_quiz_corrected"] = []
        context.user_data["quiz"] = None
        q_msg_id = context.user_data.get("question_msg_id")
        await safe_delete_message(context.bot, chat_id, q_msg_id)
        context.user_data.pop("question_msg_id", None)
        for msg_id in context.user_data.get("feedback_msgs", []):
            await safe_delete_message(context.bot, chat_id, msg_id)
        context.user_data["feedback_msgs"] = []

    lib_key = context.user_data.get("selected_library")
    library = LIBRARIES.get(lib_key) if lib_key else None
    if not library:
        try:
            await safe_edit_message(context.bot, chat_id, query.message.message_id, text=MSG("library_not_found", chat_id, context))
        except Exception:
            pass
        return
    buttons = []
    row = []
    for w in library["words"].keys():
        label = label_with_star(w, chat_id)
        row.append(InlineKeyboardButton(label, callback_data=f"word_{w}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    parent_stage = context.user_data.get("selected_stage")
    back_callback = f"stage_{parent_stage}" if parent_stage else "back_to_stages"
    
    buttons.append([InlineKeyboardButton(MSG("start_quiz_btn", chat_id, context), callback_data="start_quiz"),
                    InlineKeyboardButton(MSG("back_btn", chat_id, context), callback_data=back_callback)])
    try:
        await safe_edit_message(context.bot, chat_id, query.message.message_id,
                                text=MSG("quiz_cancelled", chat_id, context),
                                reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        await safe_send_message(context.bot, chat_id,
                                text=MSG("quiz_cancelled", chat_id, context),
                                reply_markup=InlineKeyboardMarkup(buttons))

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    profile = get_user_profile(chat_id)
    name = profile.get("user_name") or context.user_data.get("user_name", MSG("default_username", chat_id, context))
    score = profile.get("score", 0)
    completed = profile.get("completed_levels", [])
    completed_count = len(completed)
    learned = 0
    for level_key in completed:
        lib = LIBRARIES.get(level_key)
        if lib and isinstance(lib.get("words"), dict):
            learned += len(lib["words"])
    mistake_words = profile.get("mistake_words", [])
    mistake_counts = profile.get("mistake_counts", {})
    total_mistake_occurrences = sum(mistake_counts.values())
    marked = profile.get("marked_words", []) if profile else []
    max_levels = len([k for k in LIBRARIES.keys() if k.startswith("level_")])
    prog_width = 20
    filled = int((completed_count / max_levels) * prog_width) if max_levels > 0 else 0
    bar = "█" * filled + "░" * (prog_width - filled)
    score_display = int(score)
    text_lines = [
        MSG("profile_title", chat_id, context),
        f"{MSG('name_label', chat_id, context)} {name}",
        f"{MSG('score_label', chat_id, context)} {score_display}",
        "",
        f"{MSG('completed_levels_label', chat_id, context)} {completed_count} {MSG('of_label', chat_id, context)} {max_levels}",
        f"{bar}",
        f"{MSG('learned_words_label', chat_id, context)} {learned}",
        "",
        f"{MSG('mistakes_count_label', chat_id, context)} {len(mistake_words)}",
        f"{MSG('mistake_occurrences_label', chat_id, context)} {total_mistake_occurrences}",
        f"{MSG('marked_count_label', chat_id, context)} {len(marked)}",
        "",
        MSG('double_click_tip', chat_id, context)
    ]
    text = "\n".join(text_lines)
    await safe_send_message(context.bot, chat_id, text=text)

# ... (The rest of your code down to the error handler is unchanged)
# [I'm omitting it here for brevity, but it should be kept in your file.]
async def show_mistake_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    context.user_data["chat_id"] = chat_id
    profile = get_user_profile(chat_id)
    mistake_words = profile.get("mistake_words", []) if profile else []
    if not mistake_words:
        await safe_send_message(context.bot, chat_id, text=MSG("no_mistakes", chat_id, context))
        return
    markup, page, pages = build_mistake_menu_markup(mistake_words, 0, chat_id)
    await safe_send_message(context.bot, chat_id, text=MSG("my_mistakes_title", chat_id, context).format(page=page+1, pages=pages), reply_markup=markup)

def build_mistake_menu_markup(mistake_words, page: int, chat_id):
    total = len(mistake_words)
    pages = max(1, (total + MISTAKES_PER_PAGE - 1) // MISTAKES_PER_PAGE)
    page = max(0, min(page, pages - 1))
    start = page * MISTAKES_PER_PAGE
    chunk = mistake_words[start:start + MISTAKES_PER_PAGE]

    buttons = []
    row = []
    for w in chunk:
        label = label_with_star(w, chat_id)
        row.append(InlineKeyboardButton(label, callback_data=f"mistake_word_{w}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    ctrl = []
    if page > 0:
        ctrl.append(InlineKeyboardButton("⬅️ " + MSG("prev_page", chat_id, None), callback_data=f"mistakes_page_{page-1}"))
    if page < pages - 1:
        ctrl.append(InlineKeyboardButton("➡️ " + MSG("next_page", chat_id, None), callback_data=f"mistakes_page_{page+1}"))
    if ctrl:
        buttons.append(ctrl)
    buttons.append([InlineKeyboardButton(MSG("start_mistake_quiz_btn", chat_id, None), callback_data="start_mistake_quiz")])
    buttons.append([InlineKeyboardButton(MSG("view_top_mistakes_btn", chat_id, None), callback_data="view_top_mistakes")])
    buttons.append([InlineKeyboardButton(MSG("back_btn", chat_id, None), callback_data="back_to_main")])

    return InlineKeyboardMarkup(buttons), page, pages

async def handle_mistake_page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("mistakes_page_"):
        return
    try:
        page = int(data.replace("mistakes_page_", ""))
    except Exception:
        page = 0
    chat_id = query.message.chat.id if query.message else context.user_data.get("chat_id")
    profile = get_user_profile(chat_id)
    mistake_words = profile.get("mistake_words", []) if profile else []
    if not mistake_words:
        await safe_send_message(context.bot, chat_id, text=MSG("no_mistakes", chat_id, context))
        return
    markup, page, pages = build_mistake_menu_markup(mistake_words, page, chat_id)
    try:
        await safe_edit_message(context.bot, query.message.chat.id, query.message.message_id, text=MSG("my_mistakes_title", chat_id, context).format(page=page+1, pages=pages), reply_markup=markup)
    except Exception:
        await safe_send_message(context.bot, chat_id, text=MSG("my_mistakes_title", chat_id, context).format(page=page+1, pages=pages), reply_markup=markup)

async def handle_mistake_word_popup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data
    if not data.startswith("mistake_word_"):
        try:
            await query.answer()
        except Exception:
            pass
        return

    chat_id = query.message.chat.id if query.message else context.user_data.get("chat_id")
    context.user_data.setdefault("last_cb", {"data": None, "ts": 0.0})
    now_ts = time.time()
    last = context.user_data.get("last_cb", {"data": None, "ts": 0.0})
    last_data = last.get("data")
    last_ts = last.get("ts", 0.0)
    is_double = (last_data == data) and (now_ts - last_ts <= DOUBLE_CLICK_THRESHOLD)

    word = data.replace("mistake_word_", "")

    if is_double:
        cancel_scheduled_translation(context)
        profile = get_user_profile(chat_id)
        marked = profile.get("marked_words", [])
        if word in marked:
            marked.remove(word)
            msg = MSG("unmarked_msg", chat_id, context).format(word=word)
        else:
            marked.append(word)
            msg = MSG("marked_msg", chat_id, context).format(word=word)
        profile["marked_words"] = marked
        update_user_profile(chat_id, profile)
        context.user_data["last_cb"] = {"data": None, "ts": 0.0}
        try:
            await query.answer(text=msg, show_alert=False)
        except Exception:
            await safe_send_message(context.bot, chat_id, text=msg)
        try:
            msg_obj = query.message
            if msg_obj and msg_obj.reply_markup:
                page = 0
                try:
                    m = re.search(r"صفحه\s+(\d+)\s*/\s*(\d+)", msg_obj.text)
                    if m:
                        page = max(0, int(m.group(1)) - 1)
                except Exception:
                    page = 0
                profile = get_user_profile(chat_id)
                mistake_words = profile.get("mistake_words", []) if profile else []
                if not mistake_words:
                    await safe_edit_message(context.bot, query.message.chat.id, msg_obj.message_id, text=MSG("no_mistakes", chat_id, context), reply_markup=None)
                else:
                    new_markup, new_page, pages = build_mistake_menu_markup(mistake_words, page, chat_id)
                    new_text = MSG("my_mistakes_title", chat_id, context).format(page=new_page+1, pages=pages)
                    await safe_edit_message(context.bot, query.message.chat.id, msg_obj.message_id, text=new_text, reply_markup=new_markup)
        except Exception:
            pass
        return
    else:
        context.user_data["last_cb"] = {"data": data, "ts": now_ts}
        cancel_scheduled_translation(context)
        translation = None
        for lib in LIBRARIES.values():
            if word in lib.get("words", {}):
                translation = lib["words"][word]
                break
        translation = translation or MSG("translation_not_found", chat_id, context)
        task = asyncio.create_task(schedule_translation(context, chat_id, query, word, translation, DOUBLE_CLICK_THRESHOLD))
        context.user_data["last_cb_task"] = task
        return

async def handle_view_top_mistakes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id if query and query.message else context.user_data.get("chat_id")
    profile = get_user_profile(chat_id)
    counts = profile.get("mistake_counts", {}) if profile else {}
    if not counts:
        await safe_send_message(context.bot, chat_id, text=MSG("no_top_mistakes", chat_id, context))
        return
    popular = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    lines = [f"{i+1}. {w} — {c} {MSG('times_word', chat_id, context)}" for i, (w, c) in enumerate(popular[:30])]
    text = MSG("top_mistakes_title", chat_id, context) + "\n" + "\n".join(lines)
    await safe_send_message(context.bot, chat_id, text=text)

async def handle_start_mistake_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    context.user_data["chat_id"] = chat_id
    profile = get_user_profile(chat_id)
    mistake_words = profile.get("mistake_words", []) if profile else []
    if not mistake_words:
        await safe_send_message(context.bot, chat_id, text=MSG("no_mistake_quiz", chat_id, context))
        return
    items = []
    for w in mistake_words:
        tr = None
        for lib in LIBRARIES.values():
            if w in lib.get("words", {}):
                tr = lib["words"][w]
                break
        if tr:
            items.append((w, tr))
    if not items:
        await safe_send_message(context.bot, chat_id, text=MSG("no_translations_for_mistakes", chat_id, context))
        return
    questions = []
    all_distractors = [v for lib in LIBRARIES.values() for v in lib.get("words", {}).values()]
    for word, correct in items:
        options = [correct]
        while len(options) < 4 and all_distractors:
            d = random.choice(all_distractors)
            if d not in options:
                options.append(d)
        random.shuffle(options)
        questions.append({"word": word, "options": options, "answer": correct})
    context.user_data["lives"] = 3
    context.user_data.pop("header_msg_id", None)
    context.user_data.pop("question_timer_task", None)
    context.user_data["quiz"] = {"questions": questions, "current": 0, "score": 0, "mistakes": []}
    context.user_data["mistake_quiz_mode"] = True
    context.user_data["mistake_quiz_corrected"] = []
    context.user_data["feedback_msgs"] = []
    context.user_data["quiz_cancelled"] = False

    header_text = build_header_text(context.user_data["lives"], 0, len(questions), 0, chat_id, context)
    try:
        header_msg = await safe_send_message(context.bot, chat_id, text=header_text)
        if header_msg:
            context.user_data["header_msg_id"] = header_msg.message_id
    except Exception:
        context.user_data.pop("header_msg_id", None)

    await send_quiz_question(update, context)

def build_marked_menu_markup(marked_words, page: int):
    total = len(marked_words)
    pages = max(1, (total + MARKED_PER_PAGE - 1) // MARKED_PER_PAGE)
    page = max(0, min(page, pages - 1))
    start = page * MARKED_PER_PAGE
    chunk = marked_words[start:start + MARKED_PER_PAGE]

    buttons = []
    row = []
    for w in chunk:
        row.append(InlineKeyboardButton(f"{w} 🌟", callback_data=f"marked_word_{w}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    ctrl = []
    if page > 0:
        ctrl.append(InlineKeyboardButton("⬅️ " + MSG("prev_page", None, None), callback_data=f"marked_page_{page-1}"))
    if page < pages - 1:
        ctrl.append(InlineKeyboardButton("➡️ " + MSG("next_page", None, None), callback_data=f"marked_page_{page+1}"))
    if ctrl:
        buttons.append(ctrl)
    buttons.append([InlineKeyboardButton(MSG("back_btn", None, None), callback_data="back_to_main")])

    return InlineKeyboardMarkup(buttons), page, pages

async def show_marked_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    context.user_data["chat_id"] = chat_id
    profile = get_user_profile(chat_id)
    marked = profile.get("marked_words", []) if profile else []
    if not marked:
        await safe_send_message(context.bot, chat_id, text=MSG("markbook_empty", chat_id, context))
        return
    markup, page, pages = build_marked_menu_markup(marked, 0)
    await safe_send_message(context.bot, chat_id, text=MSG("markbook_title", chat_id, context).format(page=page+1, pages=pages), reply_markup=markup)

async def handle_marked_page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("marked_page_"):
        return
    try:
        page = int(data.replace("marked_page_", ""))
    except Exception:
        page = 0
    chat_id = query.message.chat.id if query.message else context.user_data.get("chat_id")
    profile = get_user_profile(chat_id)
    marked = profile.get("marked_words", []) if profile else []
    if not marked:
        await safe_send_message(context.bot, chat_id, text=MSG("markbook_empty", chat_id, context))
        return
    markup, page, pages = build_marked_menu_markup(marked, page)
    try:
        await safe_edit_message(context.bot, query.message.chat.id, query.message.message_id, text=MSG("markbook_title", chat_id, context).format(page=page+1, pages=pages), reply_markup=markup)
    except Exception:
        await safe_send_message(context.bot, chat_id, text=MSG("markbook_title", chat_id, context).format(page=page+1, pages=pages), reply_markup=markup)

async def handle_marked_word_popup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data
    if not data.startswith("marked_word_"):
        try:
            await query.answer()
        except Exception:
            pass
        return

    chat_id = query.message.chat.id if query.message else context.user_data.get("chat_id")
    context.user_data.setdefault("last_cb", {"data": None, "ts": 0.0})
    now_ts = time.time()
    last = context.user_data.get("last_cb", {"data": None, "ts": 0.0})
    last_data = last.get("data")
    last_ts = last.get("ts", 0.0)
    is_double = (last_data == data) and (now_ts - last_ts <= DOUBLE_CLICK_THRESHOLD)

    word = data.replace("marked_word_", "")

    if is_double:
        cancel_scheduled_translation(context)
        profile = get_user_profile(chat_id)
        marked = profile.get("marked_words", [])
        if word in marked:
            marked.remove(word)
            msg = MSG("unmarked_msg", chat_id, context).format(word=word)
        else:
            marked.append(word)
            msg = MSG("marked_msg", chat_id, context).format(word=word)
        profile["marked_words"] = marked
        update_user_profile(chat_id, profile)
        context.user_data["last_cb"] = {"data": None, "ts": 0.0}
        try:
            await query.answer(text=msg, show_alert=False)
        except Exception:
            await safe_send_message(context.bot, chat_id, text=msg)
        try:
            msg_obj = query.message
            if msg_obj and msg_obj.reply_markup:
                page = 0
                try:
                    m = re.search(r"صفحه\s+(\d+)\s*/\s*(\d+)", msg_obj.text)
                    if m:
                        page = max(0, int(m.group(1)) - 1)
                except Exception:
                    page = 0
                profile = get_user_profile(chat_id)
                marked_now = profile.get("marked_words", []) if profile else []
                if not marked_now:
                    await safe_edit_message(context.bot, query.message.chat.id, msg_obj.message_id, text=MSG("markbook_empty", chat_id, context), reply_markup=None)
                else:
                    new_markup, new_page, pages = build_marked_menu_markup(marked_now, page)
                    new_text = MSG("markbook_title", chat_id, context).format(page=new_page+1, pages=pages)
                    await safe_edit_message(context.bot, query.message.chat.id, msg_obj.message_id, text=new_text, reply_markup=new_markup)
        except Exception:
            pass
        return
    else:
        context.user_data["last_cb"] = {"data": data, "ts": now_ts}
        cancel_scheduled_translation(context)
        translation = None
        for lib in LIBRARIES.values():
            if word in lib.get("words", {}):
                translation = lib["words"][word]
                break
        translation = translation or MSG("translation_not_found", chat_id, context)
        task = asyncio.create_task(schedule_translation(context, chat_id, query, word, translation, DOUBLE_CLICK_THRESHOLD))
        context.user_data["last_cb_task"] = task
        return

async def start_daily_reminder_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = chat_id_from(update, context)
    context.user_data["chat_id"] = chat_id

    profile = get_user_profile(chat_id)
    current = profile.get("daily_reminder")
    if current:
        buttons = [
            [InlineKeyboardButton(MSG("change_reminder_btn", chat_id, context), callback_data="change_reminder")],
            [InlineKeyboardButton(MSG("daily_turn_off_btn", chat_id, context), callback_data="daily_turn_off")],
            [InlineKeyboardButton(MSG("back_btn", chat_id, context), callback_data="cancel_reminder_setup")]
        ]
        text = None
        try:
            text = MSG("daily_existing_msg", chat_id, context).format(timestr=current)
        except Exception:
            try:
                text = MSG("daily_set_ok", chat_id, context).format(timestr=current)
            except Exception:
                text = f"⏰ {current} (set)."
        await safe_send_message(context.bot, chat_id, text=text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    buttons = [
        [InlineKeyboardButton(MSG("back_btn", chat_id, context), callback_data="cancel_reminder_setup")]
    ]
    await safe_send_message(context.bot, chat_id, text=MSG("daily_reminder_prompt", chat_id, context), reply_markup=InlineKeyboardMarkup(buttons))
    context.user_data["awaiting_reminder_time"] = True

async def handle_time_message_for_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_reminder_time"):
        return False
    context.user_data.pop("awaiting_reminder_time", None)
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    if text.lower() == "off":
        profile = get_user_profile(chat_id)
        profile["daily_reminder"] = None
        update_user_profile(chat_id, profile)
        task = context.user_data.pop("daily_task", None)
        if task and not task.done():
            try:
                task.cancel()
            except Exception:
                pass
        await safe_send_message(context.bot, chat_id, text=MSG("daily_off", chat_id, context))
        return True
    m = TIME_RE.match(text)
    if not m:
        await safe_send_message(context.bot, chat_id, text=MSG("invalid_time_format", chat_id, context))
        return True
    hh, mm = int(m.group(1)), int(m.group(2))
    timestr = f"{hh:02d}:{mm:02d}"
    profile = get_user_profile(chat_id)
    profile["daily_reminder"] = timestr
    update_user_profile(chat_id, profile)
    task = context.user_data.get("daily_task")
    if task and not task.done():
        try:
            task.cancel()
        except Exception:
            pass
    task = asyncio.create_task(daily_reminder_loop(context.bot, chat_id, timestr, context))
    context.user_data["daily_task"] = task
    await safe_send_message(context.bot, chat_id, text=MSG("daily_set_ok", chat_id, context).format(timestr=timestr))
    return True

async def handle_cancel_reminder_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id if query.message else context.user_data.get("chat_id")
    if context.user_data.get("awaiting_reminder_time"):
        context.user_data.pop("awaiting_reminder_time", None)
    try:
        await safe_edit_message(context.bot, chat_id, query.message.message_id, text=MSG("daily_setup_cancelled", chat_id, context))
    except Exception:
        await safe_send_message(context.bot, chat_id, text=MSG("daily_setup_cancelled", chat_id, context))

async def handle_change_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id if query.message else context.user_data.get("chat_id")
    try:
        await safe_edit_message(context.bot, chat_id, query.message.message_id, text=MSG("daily_reminder_prompt", chat_id, context))
    except Exception:
        await safe_send_message(context.bot, chat_id, text=MSG("daily_reminder_prompt", chat_id, context))
    context.user_data["awaiting_reminder_time"] = True

async def handle_daily_turn_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id if query.message else context.user_data.get("chat_id")
    profile = get_user_profile(chat_id)
    profile["daily_reminder"] = None
    update_user_profile(chat_id, profile)

    task = context.user_data.pop("daily_task", None)
    if task and not task.done():
        try:
            task.cancel()
        except Exception:
            pass

    try:
        await safe_edit_message(context.bot, chat_id, query.message.message_id, text=MSG("daily_off", chat_id, context))
    except Exception:
        await safe_send_message(context.bot, chat_id, text=MSG("daily_off", chat_id, context))

async def daily_reminder_loop(bot, chat_id, timestr, context):
    try:
        hh, mm = map(int, timestr.split(":"))
        import datetime
        while True:
            now_dt = datetime.datetime.now()
            target = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now_dt:
                target = target + datetime.timedelta(days=1)
            delta = (target - now_dt).total_seconds()
            await asyncio.sleep(delta)
            profile = get_user_profile(chat_id)
            completed = set(profile.get("completed_levels", []))
            if STAGES:
                available = [lib_key for stage in STAGES.values() for lib_key in stage.get("libraries", []) if lib_key not in completed]
            else:
                available = [lib_key for lib_key in LIBRARIES.keys() if lib_key not in completed]
            if not available:
                await safe_send_message(bot, chat_id, text=MSG("all_levels_done", chat_id, context))
                profile["daily_reminder"] = None
                update_user_profile(chat_id, profile)
                return
            lib_key = random.choice(available)
            lib = LIBRARIES.get(lib_key)
            buttons = [
                [InlineKeyboardButton(MSG("yes_btn", chat_id, context), callback_data=f"daily_yes_{lib_key}")],
                [InlineKeyboardButton(MSG("no_btn", chat_id, context), callback_data=f"daily_no_{lib_key}")]
            ]
            await safe_send_message(
                bot,
                chat_id,
                text=MSG("daily_reminder_message", chat_id, context).format(title=lib['title'] if lib else lib_key),
                reply_markup=InlineKeyboardMarkup(buttons)
            )
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("daily_reminder_loop error for %s", chat_id)

async def handle_daily_yes_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("daily_yes_"):
        lib_key = data.replace("daily_yes_", "")
        await show_library_from_reminder(query, context, lib_key)
    elif data.startswith("daily_no_"):
        try:
            await safe_edit_message(context.bot, query.message.chat.id, query.message.message_id, text=MSG("daily_no_response", query.message.chat.id, context))
        except Exception:
            await safe_send_message(context.bot, query.message.chat.id, text=MSG("daily_no_response", query.message.chat.id, context))


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception: %s", context.error)
    try:
        chat_id = None
        if getattr(update, "effective_chat", None):
            chat_id = update.effective_chat.id
        elif getattr(update, "callback_query", None) and update.callback_query.message:
            chat_id = update.callback_query.message.chat.id
        if chat_id:
            await safe_send_message(context.bot, chat_id, text=MSG("generic_error", chat_id, context))
    except Exception:
        logger.exception("Failed to notify user about error")
async def combined_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    این تابع تصمیم می‌گیرد کدام هندلر inline را اجرا کند.
    اگر متنی تایپ نشده بود -> بازی سنگ-کاغذ-قیچی
    اگر متنی تایپ شده بود -> مترجم
    """
    query = update.inline_query.query
    if not query:
        # اگر کاربر چیزی تایپ نکرده، بازی را نمایش بده (از ماژول rps_game)
        await rps_game.rps_inline_query(update, context)
    else:
        # در غیر این صورت، تلاش کن متن را ترجمه کنی (از ماژول translator)
        await translator.handle_inline_translation(update, context)

# -------------------------
# Register handlers and run
if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
    raise RuntimeError("Please set TOKEN at the top of the file to your Telegram bot token")

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(handle_set_language, pattern="^set_lang_"))

# <<< تغییر ۴: جایگزینی هندلر inline قدیمی با هندلر ترکیبی جدید >>>
app.add_handler(InlineQueryHandler(combined_inline_query))
app.add_handler(CallbackQueryHandler(rps_game.handle_rps_callback, pattern="^rps_"))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu_reply))

# Callback Handlers
app.add_handler(CallbackQueryHandler(handle_stage_select, pattern="^stage_"))
app.add_handler(CallbackQueryHandler(show_words_then_quiz, pattern="^level_"))
app.add_handler(CallbackQueryHandler(handle_word_popup, pattern="^word_"))
app.add_handler(CallbackQueryHandler(handle_quiz_start_button, pattern="^start_quiz$"))
app.add_handler(CallbackQueryHandler(handle_cancel_quiz, pattern="^cancel_quiz$"))
app.add_handler(CallbackQueryHandler(handle_back_to_stages, pattern="^back_to_stages$"))
app.add_handler(CallbackQueryHandler(handle_back_to_main, pattern="^back_to_main$"))
app.add_handler(CallbackQueryHandler(handle_mistake_page_nav, pattern="^mistakes_page_"))
app.add_handler(CallbackQueryHandler(handle_mistake_word_popup, pattern="^mistake_word_"))
app.add_handler(CallbackQueryHandler(handle_start_mistake_quiz, pattern="^start_mistake_quiz$"))
app.add_handler(CallbackQueryHandler(handle_view_top_mistakes, pattern="^view_top_mistakes$"))
app.add_handler(CallbackQueryHandler(handle_marked_page_nav, pattern="^marked_page_"))
app.add_handler(CallbackQueryHandler(handle_marked_word_popup, pattern="^marked_word_"))
app.add_handler(CallbackQueryHandler(handle_daily_yes_no, pattern="^daily_yes_|^daily_no_"))
app.add_handler(CallbackQueryHandler(handle_cancel_reminder_setup, pattern="^cancel_reminder_setup$"))
app.add_handler(CallbackQueryHandler(handle_change_reminder, pattern="^change_reminder$"))
app.add_handler(CallbackQueryHandler(handle_daily_turn_off, pattern="^daily_turn_off$"))

# This handler must be last, as it has no pattern
app.add_handler(CallbackQueryHandler(handle_quiz_answer))

app.add_error_handler(global_error_handler)

if __name__ == "__main__":
    app.run_polling()