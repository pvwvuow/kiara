# rps_game.py (نسخه نهایی با قالب‌بندی quote برای لغات)
# This module contains the logic for a multiplayer vocabulary quiz game.

import uuid
import random
import asyncio
import time
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    error
)
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

# Import vocabulary data directly
from vocab_multilang import LIBRARIES

# --- Game State Management ---
active_games = {}
NUM_QUESTIONS = 5  # تعداد سوالات در هر بازی

# --- Helper Functions ---

def generate_quiz_questions(lang_code: str, difficulty: str, num_questions: int):
    if lang_code not in LIBRARIES: lang_code = 'fa'
    all_words, all_translations = [], []
    for level_data in LIBRARIES[lang_code].values():
        for word, translation in level_data.get("words", {}).items():
            all_words.append({"word": word, "answer": translation})
            all_translations.append(translation)
    if not all_words: return []
    third = len(all_words) // 3
    word_pool = {'easy': all_words[:third], 'medium': all_words[third:2*third], 'hard': all_words[2*third:]}.get(difficulty) or all_words
    if not word_pool: word_pool = all_words
    selected_items = random.sample(word_pool, min(len(word_pool), num_questions))
    questions = []
    for item in selected_items:
        correct_answer = item["answer"]
        options = {correct_answer}
        while len(options) < 4 and all_translations:
            distractor = random.choice(all_translations)
            if distractor not in options: options.add(distractor)
        shuffled_options = list(options)
        random.shuffle(shuffled_options)
        questions.append({"word": item["word"], "options": shuffled_options, "answer": correct_answer})
    return questions

def render_lobby_text(game: dict) -> str:
    initiator_name = game['players'][game['initiator_id']]['name']
    difficulty_fa = {"easy": "آسان", "medium": "متوسط", "hard": "سخت"}.get(game['difficulty'])
    player_list = "\n".join(f"✅ {p['name']}" for p in game['players'].values())
    return (
        f"🏆 **آزمون واژگان چندنفره** 🏆\n\n"
        f"بازی توسط **{initiator_name}** شروع شد.\n"
        f"سطح دشواری: **{difficulty_fa}**\n\n"
        f"**لیست انتظار بازیکنان:**\n{player_list}\n\n"
        f"⏳ منتظر پیوستن سایر بازیکنان..."
    )

def render_scoreboard(game: dict) -> str:
    """Creates a formatted scoreboard string from the current game state."""
    if not game.get('players'): return ""
    
    sorted_players = sorted(game['players'].values(), key=lambda p: p['score'], reverse=True)
    
    scoreboard_lines = ["📊 **جدول امتیازات:**"]
    for player in sorted_players:
        scoreboard_lines.append(f"👤 {player['name']}: {player['score']} امتیاز")
        
    return "\n".join(scoreboard_lines)

async def cancel_task(task):
    if task and not task.done():
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

async def question_timer_task(context: ContextTypes.DEFAULT_TYPE, inline_message_id: str, q_index: int, base_text: str, reply_markup: InlineKeyboardMarkup):
    """A dedicated asyncio task to update the timer countdown."""
    try:
        for i in range(9, 0, -1):
            if inline_message_id not in active_games or active_games[inline_message_id]['current_question'] != q_index:
                return
            await asyncio.sleep(1)
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"{base_text}\n\n**⏳ زمان باقی‌مانده: {i} ثانیه**",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            except error.BadRequest: pass # Ignore "message is not modified" error
    except asyncio.CancelledError:
        pass

async def send_next_question(context: ContextTypes.DEFAULT_TYPE, inline_message_id: str):
    if inline_message_id not in active_games: return
    game = active_games[inline_message_id]
    
    # End of game
    if game['current_question'] >= len(game['questions']):
        scoreboard_text = render_scoreboard(game)
        final_text = (
            f"🏁 **بازی تمام شد!** 🏁\n\n"
            f"**🏆 نتایج نهایی:**\n\n{scoreboard_text.replace('📊 **جدول امتیازات:**', '')}"
        )
        await context.bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=final_text,
            parse_mode=ParseMode.MARKDOWN
        )
        if inline_message_id in active_games: del active_games[inline_message_id]
        return

    # Send next question
    q_index = game['current_question']
    question_data = game['questions'][q_index]
    game['question_start_time'] = time.time()
    game['player_answers'] = {}

    buttons = [[InlineKeyboardButton(opt, callback_data=f"rps_ans_{game['game_id']}_{q_index}_{opt}")] for opt in question_data['options']]
    reply_markup = InlineKeyboardMarkup(buttons)
    
    scoreboard_text = render_scoreboard(game)
    
    # <<<--- THIS IS THE CHANGED LINE ---<<<
    base_text = (
        f"{scoreboard_text}\n"
        f"--------------------\n"
        f"سوال {q_index + 1} از {len(game['questions'])}\n\n"
        f"❓ ترجمه کلمه `{question_data['word']}` چیست؟"
    )
    
    await context.bot.edit_message_text(
        inline_message_id=inline_message_id,
        text=f"{base_text}\n\n**⏳ زمان باقی‌مانده: 10 ثانیه**",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Start tasks and wait for timeout
    timer = asyncio.create_task(question_timer_task(context, inline_message_id, q_index, base_text, reply_markup))
    timeout = asyncio.create_task(asyncio.sleep(10.2))
    game['tasks'] = {'timer': timer, 'timeout': timeout}
    await timeout
    await process_round_results(context, inline_message_id, q_index)

async def process_round_results(context: ContextTypes.DEFAULT_TYPE, inline_message_id: str, q_index: int):
    if inline_message_id not in active_games or active_games[inline_message_id]['current_question'] != q_index:
        return
    game = active_games[inline_message_id]
    
    await cancel_task(game.get('tasks', {}).get('timer'))

    correct_answer = game['questions'][q_index]['answer']
    
    for player_id, answer_data in game.get('player_answers', {}).items():
        if answer_data['choice'] == correct_answer:
            elapsed = answer_data['time'] - game['question_start_time']
            score = max(1, 10 - int(elapsed))
            game['players'][player_id]['score'] += score
            
    game['current_question'] += 1
    # Immediately send the next question with updated scores
    await send_next_question(context, inline_message_id)

# --- Main Game Handlers ---

async def rps_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if not query: return
    user = query.from_user
    lang_code = user.language_code if user.language_code in LIBRARIES else 'fa'
    difficulties = {"easy": " آسان 😊", "medium": " متوسط 🤔", "hard": " سخت 🤯"}
    
    results = []
    for key, name in difficulties.items():
        game_id = uuid.uuid4().hex[:8]
        callback_data = f"rps_join_{game_id}_{user.id}_{key}_{lang_code}"
        
        results.append(
            InlineQueryResultArticle(
                id=game_id, title=f"🎲 بازی در سطح{name}",
                description="یک آزمون واژگان چندنفره را شروع کنید!",
                input_message_content=InputTextMessageContent(
                    f"🏆 **آزمون واژگان چندنفره**\n\nسطح دشواری: **{name.strip()}**\n\nبرای ساخت لابی، روی دکمه زیر کلیک کنید.",
                    parse_mode=ParseMode.MARKDOWN
                ),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ پیوستن به بازی", callback_data=callback_data)]]),
            )
        )
    await query.answer(results, cache_time=1)

async def handle_rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    data = query.data
    inline_message_id = query.inline_message_id

    if data.startswith("rps_join_"):
        await query.answer()
        parts = data.split("_", 5)
        game_id, initiator_id, difficulty, lang_code = parts[2], int(parts[3]), parts[4], parts[5]
        
        if inline_message_id not in active_games:
            try: initiator_name = (await context.bot.get_chat(initiator_id)).first_name
            except error.TelegramError: initiator_name = "بازیکن ۱"
            active_games[inline_message_id] = {
                "game_id": game_id, "initiator_id": initiator_id, "lang_code": lang_code,
                "difficulty": difficulty, "status": "lobby", "questions": [], "current_question": 0,
                "players": {initiator_id: {"id": initiator_id, "name": initiator_name, "score": 0}},
            }

        game = active_games[inline_message_id]
        if game['status'] != 'lobby':
            await query.answer("این بازی شروع شده!", show_alert=True); return
        if user.id not in game['players']:
            game['players'][user.id] = {"id": user.id, "name": user.first_name, "score": 0}

        join_cb = f"rps_join_{game_id}_{initiator_id}_{difficulty}_{lang_code}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ پیوستن به بازی", callback_data=join_cb)],
            [InlineKeyboardButton("🚀 شروع بازی", callback_data=f"rps_start_{game_id}")]
        ])
        await query.edit_message_text(render_lobby_text(game), reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("rps_start_"):
        await query.answer()
        if not inline_message_id or inline_message_id not in active_games: return
        game = active_games[inline_message_id]
        if user.id != game['initiator_id']:
            await query.answer("فقط شروع‌کننده بازی می‌تواند آن را آغاز کند!", show_alert=True); return
        
        game['status'] = 'playing'
        game['questions'] = generate_quiz_questions(game['lang_code'], game['difficulty'], NUM_QUESTIONS)
        
        if not game['questions']:
            await query.edit_message_text("خطا: لغتی برای این سطح یافت نشد.")
            if inline_message_id in active_games: del active_games[inline_message_id]
            return

        for i in range(3, 0, -1):
            await query.edit_message_text(f"بازی تا **{i}** ثانیه دیگر شروع می‌شود... {['1️⃣','2️⃣','3️⃣'][3-i]}", parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(1)
        asyncio.create_task(send_next_question(context, inline_message_id))

    elif data.startswith("rps_ans_"):
        if not inline_message_id or inline_message_id not in active_games:
            await query.answer("این بازی دیگر فعال نیست.", show_alert=True); return
        game = active_games[inline_message_id]
        parts = data.split("_", 4)
        q_index, selected_answer = int(parts[3]), parts[4]

        if game['status'] != 'playing' or q_index != game['current_question']:
            await query.answer("این سوال دیگر فعال نیست.", show_alert=False); return
        if user.id in game.get('player_answers', {}):
            await query.answer("شما قبلاً پاسخ خود را ثبت کرده‌اید.", show_alert=False); return

        game.setdefault('player_answers', {})[user.id] = {"choice": selected_answer, "time": time.time()}
        await query.answer(f"انتخاب شما «{selected_answer}» ثبت شد.", show_alert=False)