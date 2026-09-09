import os
import threading
import datetime
import requests
from flask import Flask
from telegram import ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ---------------- CONFIG ----------------
FIREBASE_URL = os.environ.get(
    "FIREBASE_URL",
    "https://tab-munis-default-rtdb.asia-southeast1.firebasedatabase.app"
).rstrip("/")
BOT_TOKEN = os.environ["BOT_TOKEN"]

# ---------------- FIREBASE HELPERS ----------------
def clean_phone(p):
    return "".join(ch for ch in (p or "") if ch.isdigit())

def get_employees():
    r = requests.get(f"{FIREBASE_URL}/employees.json", timeout=10)
    r.raise_for_status()
    return r.json() or {}

def find_employee_by_phone(phone):
    emps = get_employees()
    target = clean_phone(phone)
    if not target:
        return None, None
    for emp_id, emp in emps.items():
        if clean_phone(emp.get("phone", "")) == target:
            return emp_id, emp
    return None, None

def find_employee_by_telegram_id(tg_id):
    emps = get_employees()
    for emp_id, emp in emps.items():
        if str(emp.get("telegramId", "")) == str(tg_id):
            return emp_id, emp
    return None, None

def set_employee_telegram_id(emp_id, tg_id):
    requests.patch(f"{FIREBASE_URL}/employees/{emp_id}.json", json={"telegramId": str(tg_id)}, timeout=10)

def mark_attendance(emp_id, date_key, status="keldi"):
    requests.put(f"{FIREBASE_URL}/attendance/{emp_id}/{date_key}.json", json=status, timeout=10)

def get_attendance(emp_id, date_key):
    r = requests.get(f"{FIREBASE_URL}/attendance/{emp_id}/{date_key}.json", timeout=10)
    if r.status_code == 200:
        return r.json()
    return None

# ---------------- KEYBOARDS ----------------
MAIN_KB = ReplyKeyboardMarkup([[KeyboardButton("✅ Ishga keldim")]], resize_keyboard=True)
CONTACT_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📱 Raqamni yuborish", request_contact=True)]],
    resize_keyboard=True, one_time_keyboard=True
)

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    emp_id, emp = find_employee_by_telegram_id(tg_id)
    if emp:
        await update.message.reply_text(
            f"Salom, {emp.get('name','')}! Xush kelibsiz.",
            reply_markup=MAIN_KB
        )
    else:
        await update.message.reply_text(
            "Assalomu alaykum! Ro'yxatdan o'tish uchun telefon raqamingizni yuboring.\n"
            "(Pastdagi tugmani bosing)",
            reply_markup=CONTACT_KB
        )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    tg_id = update.effective_user.id
    emp_id, emp = find_employee_by_phone(contact.phone_number)
    if emp_id:
        set_employee_telegram_id(emp_id, tg_id)
        await update.message.reply_text(
            f"Rahmat, {emp.get('name','')}! Ro'yxatdan muvaffaqiyatli o'tdingiz.\n"
            f"Endi har kuni ishga kelganingizda pastdagi tugmani bosing.",
            reply_markup=MAIN_KB
        )
    else:
        await update.message.reply_text(
            "Kechirasiz, bu telefon raqami bilan xodim topilmadi.\n\n"
            "Administrator (Ibrohim) ilovada sizning telefon raqamingizni to'g'ri kiritganini "
            "tekshirib, keyin qaytadan /start bosing."
        )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()
    tg_id = update.effective_user.id
    emp_id, emp = find_employee_by_telegram_id(tg_id)

    if not emp_id:
        await update.message.reply_text("Avval ro'yxatdan o'ting: /start buyrug'ini bosing.")
        return

    if "keldim" in text or "keldi" in text:
        today = datetime.date.today().isoformat()
        existing = get_attendance(emp_id, today)
        if existing == "keldi":
            await update.message.reply_text(f"Bugun ({today}) allaqachon \"Keldi\" deb belgilangansiz. ✅")
            return
        mark_attendance(emp_id, today, "keldi")
        now_str = datetime.datetime.now().strftime("%H:%M")
        await update.message.reply_text(
            f"✅ Qabul qilindi, {emp.get('name','')}!\n"
            f"Bugungi ({today}, soat {now_str}) davomatingiz \"Keldi\" deb belgilandi."
        )
    else:
        await update.message.reply_text(
            "Ishga kelganingizni belgilash uchun pastdagi \"✅ Ishga keldim\" tugmasini bosing.",
            reply_markup=MAIN_KB
        )

# ---------------- KEEP-ALIVE WEB SERVER (Render free tier) ----------------
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Tabel bot ishlayapti ✅"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

# ---------------- MAIN ----------------
def main():
    threading.Thread(target=run_flask, daemon=True).start()

    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("Bot ishga tushdi...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
