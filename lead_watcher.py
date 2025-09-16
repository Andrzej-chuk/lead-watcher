# lead_watcher.py — ключевые слова + минус-слова + отсев самопрезентаций + ИИ
# + красивые данные источника (чат/ссылка) и автора.
import os, json, re
from datetime import datetime
from telethon import events
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# ====== КОНФИГ ======
API_ID  = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
STRING   = os.getenv("TELEGRAM_STRING_SESSION", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

DEST_CHAT = os.getenv("DEST_CHAT", "Лиды")   # "Лиды", "@username", "-100ид", либо "me"
TARGET_CHATS = [x.strip() for x in os.getenv("TARGET_CHATS", "").split(",") if x.strip()]
THRESHOLD = float(os.getenv("THRESHOLD", "0.75"))
MODEL = os.getenv("MODEL", "gpt-4o-mini")

KEYWORDS = [k.strip().lower() for k in os.getenv(
    "KEYWORDS",
    "нужен, нужна, нужно, ищем, требуется, кто сделает, сделайте, автоматизация, интеграция, api, "
    "склад, складской учет, учет товара, учет остатков, crm, битрикс, bitrix, amocrm, retailcrm, "
    "учет, финучет, финансовый учет, управленческий учет, упр учет, документооборот, эдо, отчеты, аналитика, "
    "дашборд, power bi, google таблицы, скрипт, телеграм бот, парсер, парсинг, интернет-магазин, платежи, оплаты, "
    "shopify, woocommerce, tilda, маркетплейс, ozon, вайлдберриз, erp, odoo, zoho, миграция, обмен с сайтом"
).split(",")]

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
    t = (text or "").lower()
    if len(HASHTAG_RE.findall(text)) >= 3:
        return True
    if len(re.findall(r'https?://|t\.me/', text, flags=re.I)) >= 2:
        return True
    if CONTACT_RE.search(text) and any(w in t for w in PROVIDER_WORDS):
        return True
    if any(w in t for w in PROVIDER_WORDS) and any(h in t for h in FIRST_PERSON_HINTS):
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    bullets = sum(1 for ln in lines if ln.startswith(("-", "—", "•", "👉", "✅", "📌", "🔹", "🔸")))
    if bullets >= 6 and len(lines) >= 8:
        return True
    return False

# ====== Telegram ======
client = TelegramClient(StringSession(STRING), API_ID, API_HASH)

_dest_entity = None
async def _resolve_dest():
    global _dest_entity
    if _dest_entity is not None:
        return _dest_entity
    if DEST_CHAT == "me":
        _dest_entity = "me"
        return _dest_entity
    try:
        _dest_entity = await client.get_entity(DEST_CHAT)
        return _dest_entity
    except Exception:
        async for d in client.iter_dialogs():
            if d.name == DEST_CHAT:
                _dest_entity = d.entity
                return _dest_entity
    raise RuntimeError(f"Не найден получатель DEST_CHAT='{DEST_CHAT}'. Укажи точное имя, @username или -100ID.")

def _build_private_link(chat_id: int, msg_id: int) -> str:
    # для приватных супер-групп/каналов: https://t.me/c/<internal_id>/<msg_id>
    s = str(chat_id)
    return f"https://t.me/c/{s[4:]}/{msg_id}" if s.startswith("-100") else "(no link)"

# ====== Обработчик ======
@client.on(events.NewMessage(chats=TARGET_CHATS or None))
async def handler(event):
    text = (event.message.message or "").strip()
    if not text:
        return

    # 1) Авто-исключение сообщений от ботов
    try:
        sender = await event.get_sender()
        uname_sender = (getattr(sender, "username", "") or "").lower()
        if getattr(sender, "bot", False) or uname_sender.endswith("bot"):
            return
    except Exception:
        pass

    # 2) Минус-слова
    low = text.lower()
    if any(w in low for w in NEGATIVE_WORDS):
        return

    # 3) Самопрезентации/реклама услуг
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

    # === Красивые данные источника и автора ===
    chat_ent = await client.get_entity(event.chat_id)
    title = getattr(chat_ent, "title", None) or getattr(chat_ent, "first_name", "") or "unknown"
    chat_uname = getattr(chat_ent, "username", None)

    cid = getattr(event, "chat_id", None)
    msg_id = event.message.id
    if chat_uname:
        msg_link = f"https://t.me/{chat_uname}/{msg_id}"
        source_line = f"{title} (@{chat_uname}) | id: {cid}"
    else:
        msg_link = _build_private_link(cid, msg_id)
        source_line = f"{title} (private) | id: {cid}"

    sender = await event.get_sender()
    author_name = (" ".join(filter(None, [getattr(sender, 'first_name', None), getattr(sender, 'last_name', None)])) or "unknown").strip()
    author_username = getattr(sender, "username", None)
    author_line = author_name + (f" (@{author_username})" if author_username else "")

    body = (
        f"🔔 ЛИД {int(score*100)}% · {category}\n\n"
        f"{text}\n\n"
        f"Источник: {source_line}\n"
        f"Сообщение: {msg_link}\n"
        f"Автор: {author_line}\n"
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

