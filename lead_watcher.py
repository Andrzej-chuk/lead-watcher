# lead_watcher.py — фильтр ключевых слов + минус-слова + отсев самопрезентаций + ИИ (опц.)
import os, json, re
from datetime import datetime
from telethon import events
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# ====== КОНФИГ ======
# 1) По умолчанию читаем из ENV (Render/локально через $env:...).
API_ID  = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
STRING   = os.getenv("TELEGRAM_STRING_SESSION", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# 2) Если ENV пусты и вы запускаете локально — можно подставить ЗДЕСЬ (необязательно):
# API_ID = 1234567
# API_HASH = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
# STRING = "1AAABBB....очень_длинная_строка..."
# OPENAI_API_KEY = "sk-..."

DEST_CHAT = os.getenv("DEST_CHAT", "Лиды")   # "Лиды", "@username", "-100ид", либо "me"
TARGET_CHATS = [x.strip() for x in os.getenv("TARGET_CHATS", "").split(",") if x.strip()]
THRESHOLD = float(os.getenv("THRESHOLD", "0.75"))
MODEL = os.getenv("MODEL", "gpt-4o-mini")

# Ключевые слова: читаем из ENV; если нет — дефолт
KEYWORDS = [k.strip().lower() for k in os.getenv(
    "KEYWORDS",
    "нужен, нужна, нужно, ищем, требуется, кто сделает, сделайте, автоматизация, интеграция, api, "
    "склад, складской учет, учет товара, учет остатков, crm, битрикс, bitrix, amocrm, retailcrm, "
    "учет, финучет, финансовый учет, управленческий учет, упр учет, документооборот, эдо, отчеты, аналитика, "
    "дашборд, power bi, google таблицы, скрипт, телеграм бот, парсер, парсинг, интернет-магазин, платежи, оплаты, "
    "shopify, woocommerce, tilda, маркетплейс, ozon, вайлдберриз, erp, odoo, zoho, миграция, обмен с сайтом"
).split(",")]

# Минус-слова — отсекаем резюме/поиск работы
NEGATIVE_WORDS = [
    "ищу работу", "ищу подработку", "готов стажироваться", "стажер", "стажёр",
    "junior", "джун", "моё резюме", "мое резюме", "резюме", "cv", "портфолио",
    "могу выполнить тестовое", "зарплата", "оклад", "хочу зарплату",
    "full time", "полная занятость", "вакансия сотрудника", "ищу работодателя"
]

# ====== OpenAI (опционально) ======
try:
    from openai import OpenAI
    oai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    oai = None

# ====== УТИЛИТЫ ======
def _matches_keywords(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in KEYWORDS)

async def _score_with_ai(text: str) -> dict:
    """
    Возвращает {score:0..1, category, reason}.
    Если OPENAI_API_KEY не задан — возвращаем score=1.0 и пометку.
    """
    if not oai:
        return {"score": 1.0, "category": "по ключевым словам", "reason": "AI выключен (нет OPENAI_API_KEY)"}
    system = (
        "Ты классификатор лидов. Оцени, похоже ли сообщение на запрос исполнителя/подрядчика "
        "в сферах автоматизация/интеграции/учёт/склад/CRM/боты. Верни JSON: "
        "{score:0..1, category, reason (1-2 предложения)}. Запрос клиента/нанимателя — ближе к 1, флуд — ближе к 0."
    )
    user = f"Сообщение:\n{text}"
    resp = oai.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user",   "content": user}],
        temperature=0.1,
    )
    content = resp.choices[0].message.content.strip()
    try:
        start, end = content.find("{"), content.rfind("}")
        data = json.loads(content[start:end+1])
    except Exception:
        data = {"score": 0.0, "category": "unknown", "reason": "parse_error", "raw": content}
    try:
        data["score"] = max(0.0, min(1.0, float(data.get("score", 0))))
    except Exception:
        data["score"] = 0.0
    return data

# ====== Фильтр самопрезентаций/рекламы исполнителей ======
PROVIDER_WORDS = [
    "предлагаю", "окажу", "услуги", "сделаю", "настрою", "разработаю", "помогу",
    "берусь", "выполню", "возьмусь", "мои компетенции", "мой опыт", "стек", "портфолио",
    "техспец", "технический специалист", "обращайтесь", "пишите в лс", "готов взять",
    "настройка под ключ", "под ключ", "веду проекты", "занимаюсь", "предоставляю"
]
FIRST_PERSON_HINTS = ["я ", "мы "]
CONTACT_RE = re.compile(r'(\+?\d[\d\s\-\(\)]{9,}|@[\w\d_]{3,}|https?://|t\.me/|wa\.me/|vk\.me/|telegram\.me/)', re.I)
HASHTAG_RE = re.compile(r'(?:^|\s)#\w+', re.U)

def is_provider_pitch(text: str) -> bool:
    """Грубый отсев: самопрезентация, прайсы, каталоги услуг."""
    t = (text or "").lower()

    # много хэштегов/ссылок/контактов
    if len(HASHTAG_RE.findall(text)) >= 3:
        return True
    if len(re.findall(r'https?://|t\.me/', text, flags=re.I)) >= 2:
        return True
    if CONTACT_RE.search(text):
        if any(w in t for w in PROVIDER_WORDS):
            return True

    # «я/мы + сделаю/настрою/окажу …»
    if any(w in t for w in PROVIDER_WORDS) and any(h in t for h in FIRST_PERSON_HINTS):
        return True

    # маркдаун/список преимуществ (много пунктов-эмодзи/дефисов)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    bullets = sum(1 for ln in lines if ln.startswith(("-", "—", "•", "👉", "✅", "📌", "🔹", "🔸")))
    if bullets >= 6 and len(lines) >= 8:
        return True

    return False

# ====== Telegram ======
client = TelegramClient(StringSession(STRING), API_ID, API_HASH)

_dest_entity = None
async def _resolve_dest():
    """Разрешаем DEST_CHAT в entity (поддерживает 'me', @username, -100id, точное имя диалога)."""
    global _dest_entity
    if _dest_entity is not None:
        return _dest_entity
    if DEST_CHAT == "me":
        _dest_entity = "me"
        return _dest_entity
    try:
        _dest_entity = await client.get_entity(DEST_CHAT)  # @username или -100id
        return _dest_entity
    except Exception:
        async for d in client.iter_dialogs():
            if d.name == DEST_CHAT:       # точное имя диалога
                _dest_entity = d.entity
                return _dest_entity
    raise RuntimeError(f"Не найден получатель DEST_CHAT='{DEST_CHAT}'. Укажи точное имя, @username или -100ID.")

# ====== Обработчик ======
@client.on(events.NewMessage(chats=TARGET_CHATS or None))
async def handler(event):
    text = (event.message.message or "").strip()
    if not text:
        return

    # 1) Авто-исключение сообщений от ботов
    try:
        sender = await event.get_sender()
        uname = (getattr(sender, "username", "") or "").lower()
        if getattr(sender, "bot", False) or uname.endswith("bot"):
            return
    except Exception:
        pass

    # 2) Минус-слова — отрезаем резюме/поиск работы до любых затрат
    low = text.lower()
    if any(w in low for w in NEGATIVE_WORDS):
        return

    # 3) Самопрезентации/рекламные посты — отсекаем
    if is_provider_pitch(text):
        return

    # 4) Быстрый фильтр по ключевым словам
    if not _matches_keywords(text):
        return

    # 5) ИИ-оценка
    data = await _score_with_ai(text)
    score = data.get("score", 0.0)
    if score < THRESHOLD:
        return

    category = data.get("category", "unknown")
    reason = data.get("reason", "")

    # Простой источник (без ссылок/автора)
    source = getattr(getattr(event, "chat", None), "title", str(getattr(event, "chat_id", "unknown")))

    body = (
        f"🔔 ЛИД {int(score*100)}% · {category}\n\n"
        f"{text}\n\n"
        f"Источник: {source}\n"
        f"Обоснование: {reason}\n"
        f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    dest = await _resolve_dest()
    await client.send_message(dest, body)

# ====== Main ======
def main():
    for k in ("TELEGRAM_API_ID","TELEGRAM_API_HASH","TELEGRAM_STRING_SESSION","OPENAI_API_KEY"):
        if not os.getenv(k):
            print(f"⚠ Переменная {k} не установлена.")
    print("Lead watcher запущен. Нажми Ctrl+C для выхода.")
    with client:
        client.run_until_disconnected()

if __name__ == "__main__":
    main()

