import os
import math
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

WORKPLACE_LAT = float(os.environ.get("WORKPLACE_LAT", "40.979000"))
WORKPLACE_LON = float(os.environ.get("WORKPLACE_LON", "71.705056"))
RADIUS_M = float(os.environ.get("RADIUS_M", "300"))
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

WORK_START = os.environ.get("WORK_START", "08:00")  # standart ish boshlanish vaqti
FULL_DAY_HOURS = float(os.environ.get("FULL_DAY_HOURS", "10"))
TASHKENT_TZ = datetime.timezone(datetime.timedelta(hours=5))

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def compute_hours_worked(now_dt):
    """Kech qolgan soatga qarab, shu kun uchun ishlangan soatni hisoblaydi (10 soatdan kamayadi)."""
    start_h, start_m = map(int, WORK_START.split(":"))
    standard_start = now_dt.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    lateness_hours = max(0.0, (now_dt - standard_start).total_seconds() / 3600)
    hours = max(0.0, FULL_DAY_HOURS - lateness_hours)
    return round(hours, 1), round(lateness_hours, 1)

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

def mark_attendance(emp_id, date_key, status, hours):
    requests.put(f"{FIREBASE_URL}/attendance/{emp_id}/{date_key}.json", json={"s": status, "h": hours}, timeout=10)

def get_attendance(emp_id, date_key):
    r = requests.get(f"{FIREBASE_URL}/attendance/{emp_id}/{date_key}.json", timeout=10)
    if r.status_code == 200:
        return r.json()
    return None

# ---------------- KEYBOARDS ----------------
MAIN_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📍 Ishga keldim", request_location=True)]],
    resize_keyboard=True
)
CONTACT_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📱 Raqamni yuborish", request_contact=True)]],
    resize_keyboard=True, one_time_keyboard=True
)

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    emp_id, emp = find_employee_by_telegram_id(tg_id)
    if emp:
        await update.message.reply_text(f"Salom, {emp.get('name','')}! Xush kelibsiz.", reply_markup=MAIN_KB)
    else:
        await update.message.reply_text(
            "Assalomu alaykum! Ro'yxatdan o'tish uchun telefon raqamingizni yuboring.\n(Pastdagi tugmani bosing)",
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

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    emp_id, emp = find_employee_by_telegram_id(tg_id)
    if not emp_id:
        await update.message.reply_text("Avval ro'yxatdan o'ting: /start buyrug'ini bosing.")
        return

    loc = update.message.location
    distance = haversine_m(loc.latitude, loc.longitude, WORKPLACE_LAT, WORKPLACE_LON)

    if distance > RADIUS_M:
        await update.message.reply_text(
            f"❌ Siz ish joyidan {int(distance)} metr uzoqdasiz.\n"
            f"Davomat faqat ish joyida turib belgilanadi. Ish joyiga yetgach, qaytadan urinib ko'ring."
        )
        if ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(
                    ADMIN_CHAT_ID,
                    f"⚠️ {emp.get('name','')} \"Ishga keldim\" deb yozdi, lekin ish joyidan "
                    f"{int(distance)} metr uzoqda edi (belgilanmadi)."
                )
            except Exception:
                pass
        return

    today_dt = datetime.datetime.now(TASHKENT_TZ)
    today = today_dt.date().isoformat()
    existing = get_attendance(emp_id, today)
    if existing and existing.get("s") == "keldi":
        await update.message.reply_text(f"Bugun ({today}) allaqachon \"Keldi\" deb belgilangansiz. ✅")
        return

    hours, lateness = compute_hours_worked(today_dt)
    mark_attendance(emp_id, today, "keldi", hours)
    now_str = today_dt.strftime("%H:%M")

    if lateness > 0:
        msg = (
            f"✅ Qabul qilindi, {emp.get('name','')}!\n"
            f"Kelgan vaqt: {now_str} (standart boshlanishdan {lateness} soat kech)\n"
            f"Bugungi ishlagan soatingiz: {hours} soat (10 soatdan kamaytirilgan)."
        )
    else:
        msg = (
            f"✅ Qabul qilindi, {emp.get('name','')}!\n"
            f"Kelgan vaqt: {now_str}\nBugungi ishlagan soatingiz: {hours} soat (to'liq kun)."
        )
    await update.message.reply_text(msg)

    if ADMIN_CHAT_ID and lateness > 0:
        try:
            await context.bot.send_message(
                ADMIN_CHAT_ID,
                f"ℹ️ {emp.get('name','')} bugun {now_str} da keldi ({lateness} soat kech). "
                f"Hisoblangan soat: {hours}."
            )
        except Exception:
            pass

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    emp_id, emp = find_employee_by_telegram_id(tg_id)
    if not emp_id:
        await update.message.reply_text("Avval ro'yxatdan o'ting: /start buyrug'ini bosing.")
        return
    await update.message.reply_text(
        "Ishga kelganingizni belgilash uchun pastdagi \"📍 Ishga keldim\" tugmasini bosing "
        "(joylashuvingiz so'raladi).",
        reply_markup=MAIN_KB
    )

# ---------------- KEEP-ALIVE WEB SERVER ----------------
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
    application.add_handler(MessageHandler(filters.LOCATION, location_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("Bot ishga tushdi...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
