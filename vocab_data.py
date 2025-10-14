# vocab_data.py
# لودر چندزبانه با استفاده از یک فایل مرکزی

from typing import Dict
from vocab_multilang import LIBRARIES as ALL_LIBRARIES, STAGES as ALL_STAGES

# نگاشت زبان به بسته‌های کتابخانه و مرحله
LANG_MAP: Dict[str, Dict[str, Dict]] = {
    lang: {
        "LIBRARIES": ALL_LIBRARIES.get(lang, {}),
        "STAGES": ALL_STAGES.get(lang, {})
    }
    for lang in set(ALL_LIBRARIES.keys()) | set(ALL_STAGES.keys())
}

# زبان پیش‌فرض
DEFAULT_LANG = "fa"

def get_lang_pack(lang_code: str):
    """
    بازگرداندن dict شامل 'LIBRARIES' و 'STAGES' برای lang_code.
    اگر lang_code معتبر نبود، بستهٔ پیش‌فرض بازگردانده می‌شود.
    """
    return LANG_MAP.get(lang_code, LANG_MAP.get(DEFAULT_LANG, {"LIBRARIES": {}, "STAGES": {}}))

def get_libraries(lang_code: str):
    """بازگرداندن LIBRARIES مربوط به زبان"""
    return get_lang_pack(lang_code).get("LIBRARIES", {})

def get_stages(lang_code: str):
    """بازگرداندن STAGES مربوط به زبان"""
    return get_lang_pack(lang_code).get("STAGES", {})

# مقادیر پیش‌فرض برای سازگاری با import مستقیم
LIBRARIES = get_libraries(DEFAULT_LANG)
STAGES = get_stages(DEFAULT_LANG)

def set_default_lang(lang_code: str):
    """
    تغییر زبان پیش‌فرض برای LIBRARIES و STAGES.
    پس از فراخوانی، مقادیر سراسری بازنویسی می‌شوند.
    """
    global DEFAULT_LANG, LIBRARIES, STAGES
    if lang_code in LANG_MAP:
        DEFAULT_LANG = lang_code
    LIBRARIES = get_libraries(DEFAULT_LANG)
    STAGES = get_stages(DEFAULT_LANG)