import requests
import logging
import uuid
from typing import Optional
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ContextTypes

# --- تنظیمات ---
# ما از یکی از سرورهای عمومی LibreTranslate استفاده می‌کنیم
TRANSLATE_API_URL = "https://translate.argosopentech.com/translate" 
logger = logging.getLogger(__name__)

def get_translation(text: str) -> Optional[str]:
    """
    متن را با استفاده از API عمومی LibreTranslate از فارسی به انگلیسی ترجمه می‌کند.
    """
    if not text or not text.strip():
        return None
    
    # داده‌هایی که باید به سرور ارسال شود
    payload = {
        "q": text,
        "source": "fa",
        "target": "en",
        "format": "text"
    }
    
    try:
        # ارسال درخواست به سرور
        response = requests.post(TRANSLATE_API_URL, json=payload, timeout=7)
        
        if response.status_code == 200:
            data = response.json()
            translated_text = data.get("translatedText")
            return translated_text
        else:
            logger.warning(f"LibreTranslate API - Failed. Status: {response.status_code}, Response: {response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"LibreTranslate API - Network error: {e}")
        return None

async def handle_inline_translation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    این تابع هیچ تغییری نکرده است.
    """
    query = update.inline_query.query
    
    if not query:
        return
        
    translation = get_translation(query)
    
    results = []
    if translation:
        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="ترجمه به انگلیسی",
                description=translation,
                input_message_content=InputTextMessageContent(translation)
            )
        )
    
    if results:
        await update.inline_query.answer(results, cache_time=5)