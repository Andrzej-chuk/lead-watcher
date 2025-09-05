# lead_watcher.py — чистая версия с правильными отступами
import os, json
from datetime import datetime
from telethon import events
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from openai import OpenAI

# ==== НАСТРОЙКИ ====
# Вариант 1: берём из переменных окружения (рекомендуется)
API_ID  = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
STRING   = os.getenv("TELEGRAM_STRING_SESSION", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Вариант 2: если ENV пусты — подставь сюда свои значения (иначе оставь пустым)
if not API_ID or not API_HASH or not STRING:
    API_ID = API_ID or 0               # пример: 26393382  (числом)
    API_HASH = API_HASH or ""          # пример: "49344b12bblab04d882c1dc7737cfdfd"
    STRING = STRING or ""              # пример: "1ApwazpM..."
if not OPENAI_API_KEY:
    OPENAI_API_KEY = ""                # пример: "sk-..."

DEST_CHAT = os.getenv("DEST_CHAT", "Лиды")   # "me", точное имя, @username или -100ID
TARGET_CHATS = [x.strip() for x in os.getenv("TARGET_CHATS", "").split(",") if x.strip()]
THRESHOLD = float(os.getenv("THRESHOLD", "0.75"))
MODEL = os.getenv("MODEL", "gpt-4o-mini")
KEYWORDS = [k.strip().lower() for k in os.getenv(
    "KEYWORDS",
    "1с, автоматизация, интеграция, bitrix, crm, erp, склад, разработчик, учет, документооборот, api, бот, telegram"
).split(",")]

# ==== КЛИЕНТЫ ====
oai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
client = TelegramClient(StringSession(STRING), API_ID, API_HASH)

# ==== УТИЛИТЫ ====
def _matches_keywords(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in KEYWORDS)

async def _score_with_ai(text: str) -> dict:
    """Вернуть {score:0..1, category, reason}. Если нет ключа — имитируем 1.0."""
    if not oai:
        return {"score": 1.0, "category": "по ключевым словам", "reason": "AI выключен (нет OPENAI_API_KEY)"}
    system = (
        "Ты классификатор лидов. Оцени, похоже ли сообщение на запрос исполнителя/подрядчика "
        "в сферах ИТ/автоматизация/1С/CRM/интеграции. Верни JSON: "
        "{score:0..1, category, reason (1-2 предложения)}. "
        "Запрос клиента/нанимателя — ближе к 1, флуд — ближе к 0."
    )
    user = f"Сообщение:\n{text}"
    resp = oai.chat.completions.create(
        model=MODEL,
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=0.1,
    )
    content = resp.choices[0].message.content.strip()
    try:
        start, end = content.find("{"), content.rfind("}")
        data = json.loads(content[start:end+1])
    except Exception:
        data = {"score":0.0, "category":"unknown", "reason":"parse_error", "raw":content}
    try:
        data["score"] = max(0.0, min(1.0, float(data.get("score", 0))))
    except:
        data["score"] = 0.0
    return data

# --- resolver получателя (канал "Лиды"/me/@username/-100ID) ---
_dest_entity = None
async def _resolve_dest():
    global _dest_entity
    if _dest_entity is not None:
        return _dest_entity
    if DEST_CHAT == "me":
        _dest_entity = "me"
        return _dest_entity
    try:
        _dest_entity = await client.get_entity(DEST_CHAT)   # ВАЖНО: await
        return _dest_entity
    except Exception:
        async for d in client.iter_dialogs():
            if d.name == DEST_CHAT:
                _dest_entity = d.entity
                return _dest_entity
    raise RuntimeError(f"Не найден получатель DEST_CHAT='{DEST_CHAT}'. Укажи точное имя, @username или -100ID.")

# ==== ОБРАБОТЧИК ====
@client.on(events.NewMessage(chats=TARGET_CHATS or None))
async def handler(event):
    text = (event.message.message or "").strip()
    if not text or not _matches_keywords(text):
        return

    data = await _score_with_ai(text)
    score = data.get("score", 0.0)
    if score < THRESHOLD:
        return

    category = data.get("category", "unknown")
    reason = data.get("reason", "")

    # читаемый источник + кликабельная ссылка + автор
    chat_ent = await client.get_entity(event.chat_id)
    title = getattr(chat_ent, "title", None) or getattr(chat_ent, "first_name", "") or "unknown"
    uname = getattr(chat_ent, "username", None)

    cid = getattr(event, "chat_id", None)
    msg_id = event.message.id
    if uname:
        msg_link = f"https://t.me/{uname}/{msg_id}"
    else:
        cid_str = str(cid)
        msg_link = f"https://t.me/c/{cid_str[4:]}/{msg_id}" if cid_str.startswith("-100") else "(no link)"

    source_line = f"{title}" + (f" (@{uname})" if uname else "") + f" | id: {cid}"

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

# ==== MAIN ====
def main():
    for k in ("TELEGRAM_API_ID","TELEGRAM_API_HASH","TELEGRAM_STRING_SESSION","OPENAI_API_KEY"):
        if not os.getenv(k):
            print(f"⚠ Переменная {k} не установлена.")
    print("Lead watcher запущен. Нажми Ctrl+C для выхода.")
    with client:
        client.run_until_disconnected()

if __name__ == "__main__":
    main()
