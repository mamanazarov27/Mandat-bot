# -*- coding: utf-8 -*-
"""
mandat.uzbmb.uz uchun Telegram bot — foydalanuvchi fanlarni tugmalar orqali
tanlaydi, ID kiritadi, bot esa avtomatik qidirib o'rnini (rank) topib beradi.

O'RNATISH:
    pip install python-telegram-bot requests beautifulsoup4

ISHGA TUSHIRISH:
    Windows/Linux/Mac konsolida (yoki hosting'da) muhit o'zgaruvchisi
    sifatida bot tokenini bering:

        BOT_TOKEN=1234567890:AA... python bot.py

    yoki pastdagi BOT_TOKEN qatoriga to'g'ridan-to'g'ri yozing (hosting'da
    esa har doim Environment Variable orqali berish tavsiya etiladi).
"""

import os
import asyncio
import time
import requests
from bs4 import BeautifulSoup

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "SHU_YERGA_TOKENINGIZNI_YOZING")

BASE = "https://mandat.uzbmb.uz"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": f"{BASE}/Bakalavr",
    "Accept-Language": "uz,ru;q=0.9,en;q=0.8",
}

CHOOSE_S4, CHOOSE_S5, CHOOSE_EDLANG, WAIT_ID = range(4)


# ---------- Saytdan ma'lumot olish funksiyalari ----------

def api_get(path, params, max_retries=6):
    wait = 2
    for _ in range(max_retries):
        r = requests.get(f"{BASE}{path}", params=params, headers=HEADERS, timeout=20)
        if r.status_code == 429:
            time.sleep(wait)
            wait = min(wait * 1.7, 20)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Server band (429), urinishlar tugadi. Birozdan so'ng qayta urinib ko'ring.")

def get_s4_subjects():
    return api_get("/Bakalavr/GetS4Subjects", {"lang": "uz"})

def get_s5_subjects(s4subject):
    return api_get("/Bakalavr/GetS5Subjects", {"lang": "uz", "s4subject": s4subject})

def get_ed_langs(s4subject, s5subject):
    data = api_get("/Bakalavr/GetEducLangs",
                    {"lang": "uz", "s4subject": s4subject, "s5subject": s5subject})
    out = []
    for item in data:
        eid = item.get("edLangId", item.get("EdLangId"))
        name = item.get("educlanguage", item.get("Educlanguage"))
        out.append((str(eid), name))
    return out


# ---------- Sahifalarni skanerlash (avvalgi skriptdagi mantiq) ----------

def get_page(page_number, page_size, s4subject, s5subject, ed_lang_id):
    params = {
        "pageNumber": page_number, "pageSize": page_size,
        "s4subject": s4subject, "s5subject": s5subject,
        "edLangId": ed_lang_id, "lang": "uz",
    }
    wait = 3
    for _ in range(8):
        r = requests.get(f"{BASE}/Bakalavr/Paginate", params=params,
                          headers=HEADERS, timeout=20)
        if r.status_code == 429:
            time.sleep(wait)
            wait = min(wait * 1.7, 30)
            continue
        r.raise_for_status()
        return r.text
    raise RuntimeError("Server band, urinishlar tugadi.")

def parse_entries(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select(".m3-rescard"):
        name_el = card.select_one(".m3-rescard__name")
        id_el = card.select_one(".m3-rescard__id")
        score_el = card.select_one(".m3-score-val")
        if not (name_el and id_el):
            continue
        name = name_el.get_text(strip=True)
        eid = id_el.get_text(strip=True).replace("#", "").strip()
        score = score_el.get_text(strip=True) if score_el else "?"
        out.append((name, eid, score))
    return out

def find_page_size(s4subject, s5subject, ed_lang_id):
    for requested in [1000, 500, 300, 200, 100, 50, 30, 20, 10]:
        try:
            html = get_page(1, requested, s4subject, s5subject, ed_lang_id)
            entries = parse_entries(html)
            if not entries:
                continue
            actual = len(entries)
            html2 = get_page(2, actual, s4subject, s5subject, ed_lang_id)
            entries2 = parse_entries(html2)
            if entries2 and entries2[0][1] != entries[0][1]:
                return actual
        except Exception:
            continue
    return 10

def find_rank(entrant_id, s4subject, s5subject, ed_lang_id, progress_cb=None):
    """Rank topguncha sahifalarni tekshiradi. progress_cb(total_seen) chaqiriladi."""
    page_size = find_page_size(s4subject, s5subject, ed_lang_id)
    page, total_seen = 1, 0
    while True:
        html = get_page(page, page_size, s4subject, s5subject, ed_lang_id)
        entries = parse_entries(html)
        if not entries:
            return None
        for name, eid, score in entries:
            total_seen += 1
            if eid == str(entrant_id):
                return {"name": name, "id": eid, "score": score, "rank": total_seen}
        if progress_cb:
            progress_cb(total_seen)
        page += 1
        time.sleep(0.2)


# ---------- Telegram bot handlerlari ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subjects = await asyncio.to_thread(get_s4_subjects)
    context.user_data["s4_list"] = subjects
    buttons = [[InlineKeyboardButton(s, callback_data=f"s4:{i}")]
               for i, s in enumerate(subjects)]
    await update.message.reply_text(
        "Salom! Bu bot mandat.uzbmb.uz saytida sizning necha-nchi o'rinda "
        "turganingizni topib beradi.\n\n1-mutaxassislik fanini tanlang:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOOSE_S4

async def choose_s4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    s4 = context.user_data["s4_list"][idx]
    context.user_data["s4subject"] = s4

    subjects = await asyncio.to_thread(get_s5_subjects, s4)
    context.user_data["s5_list"] = subjects
    buttons = [[InlineKeyboardButton(s, callback_data=f"s5:{i}")]
               for i, s in enumerate(subjects)]
    await q.edit_message_text(
        f"1-fan: {s4}\n\n2-mutaxassislik fanini tanlang:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOOSE_S5

async def choose_s5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    s5 = context.user_data["s5_list"][idx]
    context.user_data["s5subject"] = s5

    pairs = await asyncio.to_thread(
        get_ed_langs, context.user_data["s4subject"], s5)
    context.user_data["edlang_list"] = pairs
    buttons = [[InlineKeyboardButton(name, callback_data=f"el:{i}")]
               for i, (eid, name) in enumerate(pairs)]
    await q.edit_message_text(
        f"1-fan: {context.user_data['s4subject']}\n2-fan: {s5}\n\n"
        "Ta'lim tilini tanlang:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOOSE_EDLANG

async def choose_edlang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    eid, name = context.user_data["edlang_list"][idx]
    context.user_data["ed_lang_id"] = eid

    await q.edit_message_text(
        f"1-fan: {context.user_data['s4subject']}\n"
        f"2-fan: {context.user_data['s5subject']}\n"
        f"Ta'lim tili: {name}\n\n"
        "Endi 7 xonali abituriyent ID raqamingizni yozing:"
    )
    return WAIT_ID

async def got_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entrant_id = update.message.text.strip()
    if not entrant_id.isdigit():
        await update.message.reply_text("Iltimos, faqat raqam kiriting (masalan 6257398).")
        return WAIT_ID

    msg = await update.message.reply_text("Qidirilmoqda, biroz kuting... ⏳")

    loop = asyncio.get_event_loop()
    last_edit = [0]

    def progress(total_seen):
        now = time.time()
        if now - last_edit[0] > 4:  # juda tez-tez edit qilmaymiz
            last_edit[0] = now
            asyncio.run_coroutine_threadsafe(
                msg.edit_text(f"Qidirilmoqda... {total_seen} ta odam tekshirildi ⏳"),
                loop
            )

    try:
        result = await asyncio.to_thread(
            find_rank, entrant_id,
            context.user_data["s4subject"],
            context.user_data["s5subject"],
            context.user_data["ed_lang_id"],
            progress
        )
    except Exception as e:
        await msg.edit_text(f"Xatolik yuz berdi: {e}\n/start bilan qayta urinib ko'ring.")
        return ConversationHandler.END

    if result is None:
        await msg.edit_text(
            "Bu ID shu fanlar bo'yicha ro'yxatda topilmadi. "
            "ID yoki fanlarni tekshirib, /start bilan qayta urinib ko'ring."
        )
    else:
        await msg.edit_text(
            "✅ TOPILDI!\n\n"
            f"👤 Ism: {result['name']}\n"
            f"🆔 ID: {result['id']}\n"
            f"🎯 Ball: {result['score']}\n"
            f"🏆 O'rni: {result['rank']}-o'rin\n\n"
            "Boshqa fan bo'yicha tekshirish uchun /start yozing."
        )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bekor qilindi. Qayta boshlash uchun /start yozing.")
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"[XATOLIK] {context.error}")
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "Kechirasiz, kutilmagan xatolik yuz berdi. /start bilan qayta urinib ko'ring."
            )
    except Exception:
        pass


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_S4: [CallbackQueryHandler(choose_s4, pattern=r"^s4:")],
            CHOOSE_S5: [CallbackQueryHandler(choose_s5, pattern=r"^s5:")],
            CHOOSE_EDLANG: [CallbackQueryHandler(choose_edlang, pattern=r"^el:")],
            WAIT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    app.add_error_handler(error_handler)
    print("Bot ishga tushdi...")
    app.run_polling()

if __name__ == "__main__":
    main()
