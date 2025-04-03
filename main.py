import sqlite3
import re
import time
from datetime import datetime, timedelta
import pytz
import requests
from dotenv import load_dotenv
import os
import logging
import jdatetime
import uuid
import csv

# بارگذاری متغیرهای محیطی از فایل .env
load_dotenv()
BOT_TOKEN = os.getenv("BALETOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
if ADMIN_USER_ID is None:
    raise ValueError("متغیر محیطی ADMIN_USER_ID در فایل .env تعریف نشده است.")
ADMIN_USER_ID = int(ADMIN_USER_ID)
BASE_URL = f"https://tapi.bale.ai/bot{BOT_TOKEN}"

# اتصال به پایگاه داده
conn = sqlite3.connect("barbershop.db", check_same_thread=False)
cursor = conn.cursor()

# ایجاد جدول نوبت‌ها
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    barber_id INTEGER,
    date TEXT,
    time TEXT,
    service TEXT,
    name TEXT,
    phone TEXT,
    status TEXT DEFAULT 'خالی',
    payment_status TEXT DEFAULT 'پرداخت نشده',
    tracking_code TEXT,
    FOREIGN KEY(barber_id) REFERENCES barbers(id)
)
"""
)

# ایجاد جدول آرایشگرها
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS barbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    phone TEXT,
    address TEXT,
    user_id INTEGER UNIQUE,  -- اضافه کردن UNIQUE به user_id
    card_number TEXT
)
"""
)

# ایجاد جدول user_appointments برای ذخیره‌سازی اطلاعات کاربران و نوبت‌ها
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS user_appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    barber_id INTEGER,
    date TEXT,
    time TEXT,
    service TEXT,
    name TEXT,
    phone TEXT,
    status TEXT DEFAULT 'رزرو',
    payment_status TEXT DEFAULT 'پرداخت نشده',
    tracking_code TEXT
)
"""
)
conn.commit()

# تعریف ساعات کاری
working_hours = [
    "08:00",
    "09:00",
    "10:00",
    "11:00",
    "12:00",
    "13:00",
    "16:00",
    "17:00",
    "18:00",
    "19:00",
    "20:00",
]

# منطقه زمانی ثابت (تهران)
USER_TIMEZONE = "Asia/Tehran"

# تنظیمات لاگ‌گیری
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# تابع برای تبدیل تاریخ میلادی به شمسی
def to_jalali(date):
    return jdatetime.date.fromgregorian(date=date).strftime("%Y-%m-%d")


# تابع برای به‌روزرسانی جدول نوبت‌ها
def update_appointments_table():
    today = datetime.now(pytz.timezone(USER_TIMEZONE)).date()
    dates = [today + timedelta(days=i) for i in range(3)]
    dates_str = [to_jalali(date) for date in dates]

    # حذف نوبت‌های قدیمی
    cursor.execute("DELETE FROM appointments WHERE date NOT IN (?, ?, ?)", dates_str)
    conn.commit()

    # دریافت لیست آرایشگرها
    cursor.execute("SELECT id FROM barbers")
    barbers = cursor.fetchall()

    # ایجاد نوبت‌ها برای هر آرایشگر
    for barber in barbers:
        barber_id = barber[0]
        for date in dates_str:
            for time in working_hours:
                cursor.execute(
                    "SELECT * FROM appointments WHERE date=? AND time=? AND barber_id=?",
                    (date, time, barber_id),
                )
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO appointments (date, time, status, barber_id) VALUES (?, ?, ?, ?)",
                        (date, time, "خالی", barber_id),  # تنظیم barber_id برای هر نوبت
                    )
    conn.commit()


# تابع برای فیلتر کردن زمان‌های گذشته
def filter_past_times(date):
    user_tz = pytz.timezone(USER_TIMEZONE)
    now = datetime.now(user_tz)

    # اگر تاریخ امروز باشد، زمان‌های گذشته را فیلتر کن
    if date == to_jalali(now.date()):
        current_time = now.strftime("%H:%M")
        return [time for time in working_hours if time >= current_time]

    # برای فردا و پس‌فردا، همه زمان‌ها را برگردان
    return working_hours


# تابع برای ارسال پیام به کاربر
def send_message(chat_id, text, reply_markup=None):
    if reply_markup is None:
        reply_markup = {
            "inline_keyboard": [
                [{"text": "بازگشت به صفحه اصلی", "callback_data": "start"}]
            ]
        }
    else:
        reply_markup["inline_keyboard"].append(
            [{"text": "بازگشت به صفحه اصلی", "callback_data": "start"}]
        )

    url = f"{BASE_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"Message sent to {chat_id}: {text}")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending message to {chat_id}: {e}")
        return None


# تابع برای ارسال فاکتور پرداخت
def send_invoice(chat_id, amount, description, barber_id):
    cursor.execute("SELECT card_number FROM barbers WHERE id=?", (barber_id,))
    barber = cursor.fetchone()
    if not barber:
        send_message(chat_id, "خطا در دریافت اطلاعات آرایشگر.")
        return None
    print(barber)
    card_number = barber[0]
    url = f"{BASE_URL}/sendInvoice"
    payload = {
        "chat_id": chat_id,
        "title": "پرداخت هزینه خدمت",
        "description": description,
        "payload": str(uuid.uuid4()),
        "provider_token": card_number,
        "currency": "IRR",
        "prices": [{"label": "هزینه خدمت", "amount": amount}],
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"Invoice sent to {chat_id}")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending invoice to {chat_id}: {e}")
        return None


# تابع برای دریافت آخرین آپدیت‌ها
def get_updates(offset=None):
    url = f"{BASE_URL}/getUpdates"
    params = {"timeout": 30, "offset": offset}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error getting updates: {e}")
        return {"ok": False, "result": []}


# تابع برای بروزرسانی اطلاعات آرایشگرها از فایل CSV
def update_barbers_from_csv(file_path):
    with open(file_path, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            cursor.execute(
                """
                INSERT INTO barbers (name, phone, address, user_id, card_number)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                phone=excluded.phone,
                address=excluded.address,
                card_number=excluded.card_number
                """,
                (
                    row["name"],
                    row["phone"],
                    row["address"],
                    row["user_id"],
                    row["card_number"],
                ),
            )
        conn.commit()
        update_appointments_table()


# تابع برای نمایش لیست آرایشگرها
def show_barbers(chat_id):
    cursor.execute("SELECT id, name, address FROM barbers")
    barbers = cursor.fetchall()

    if barbers:
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": f"{barber[1]} - {barber[2]}",
                        "callback_data": f"select_barber_{barber[0]}",
                    }
                ]
                for barber in barbers
            ]
        }
        send_message(
            chat_id, "لطفا آرایشگر مورد نظر خود را انتخاب کنید:", reply_markup=keyboard
        )
    else:
        send_message(chat_id, "هیچ آرایشگری ثبت نشده است.")


# تابع برای نمایش نوبت‌های خالی
def show_available_slots(chat_id, barber_id):
    available_slots = []
    table = "جدول نوبت‌های خالی:\n"
    index = 1
    for date_label, date_value in [
        ("امروز", to_jalali(datetime.now(pytz.timezone(USER_TIMEZONE)).date())),
        (
            "فردا",
            to_jalali(
                (datetime.now(pytz.timezone(USER_TIMEZONE)) + timedelta(days=1)).date()
            ),
        ),
        (
            "پس‌فردا",
            to_jalali(
                (datetime.now(pytz.timezone(USER_TIMEZONE)) + timedelta(days=2)).date()
            ),
        ),
    ]:
        table += f"\n{date_label}:\n"
        filtered_times = filter_past_times(date_value)  # فیلتر زمان‌ها بر اساس تاریخ
        for time in filtered_times:
            cursor.execute(
                'SELECT * FROM appointments WHERE date=? AND time=? AND status="خالی" AND barber_id=?',
                (date_value, time, barber_id),
            )
            if cursor.fetchone():
                table += f"{index}. {time}\n"
                available_slots.append((date_value, time))
                index += 1

    if available_slots:
        context["user_data"]["available_slots"] = available_slots
        send_message(chat_id, table)
        send_message(chat_id, "لطفا شماره ردیف نوبت مدنظر خود را وارد کنید:")
        context["user_data"]["awaiting_slot_selection"] = True
    else:
        send_message(chat_id, "نوبت خالی یافت نشد.")


# تابع برای نمایش نوبت‌های خالی متوالی برای خدمات VIP
def show_vip_available_slots(chat_id, barber_id):
    available_slots = []
    table = "جدول نوبت‌های خالی متوالی (برای خدمات VIP):\n"
    index = 1
    for date_label, date_value in [
        ("امروز", to_jalali(datetime.now(pytz.timezone(USER_TIMEZONE)).date())),
        (
            "فردا",
            to_jalali(
                (datetime.now(pytz.timezone(USER_TIMEZONE)) + timedelta(days=1)).date()
            ),
        ),
        (
            "پس‌فردا",
            to_jalali(
                (datetime.now(pytz.timezone(USER_TIMEZONE)) + timedelta(days=2)).date()
            ),
        ),
    ]:
        table += f"\n{date_label}:\n"
        filtered_times = filter_past_times(date_value)  # فیلتر زمان‌ها بر اساس تاریخ
        for i in range(len(filtered_times) - 1):
            time1 = filtered_times[i]
            time2 = filtered_times[i + 1]
            if time1 == "13:00" and time2 == "16:00":
                continue
            cursor.execute(
                'SELECT * FROM appointments WHERE date=? AND time=? AND status="خالی" AND barber_id=?',
                (date_value, time1, barber_id),
            )
            slot1 = cursor.fetchone()
            cursor.execute(
                'SELECT * FROM appointments WHERE date=? AND time=? AND status="خالی" AND barber_id=?',
                (date_value, time2, barber_id),
            )
            slot2 = cursor.fetchone()
            if slot1 and slot2:
                table += f"{index}. {time1} و {time2}\n"
                available_slots.append((date_value, time1))
                index += 1

    if available_slots:
        context["user_data"]["available_slots"] = available_slots
        send_message(chat_id, table)
        send_message(chat_id, "لطفا شماره ردیف نوبت مدنظر خود را وارد کنید:")
        context["user_data"]["awaiting_slot_selection"] = True
    else:
        send_message(chat_id, "نوبت خالی متوالی یافت نشد.")


# تابع برای لغو نوبت
def cancel_appointment(user_id):
    # دریافت اطلاعات نوبت کاربر از دیتابیس
    cursor.execute(
        "SELECT barber_id, date, time FROM user_appointments WHERE user_id=? AND status='رزرو'",
        (user_id,),
    )
    appointment = cursor.fetchone()

    if appointment:
        barber_id, date, time = appointment
        # لغو نوبت در جدول appointments
        cursor.execute(
            "UPDATE appointments SET user_id=NULL, name=NULL, phone=NULL, service=NULL, status='خالی' WHERE date=? AND time=? AND barber_id=?",
            (date, time, barber_id),
        )
        # لغو نوبت در جدول user_appointments
        cursor.execute(
            "UPDATE user_appointments SET status='لغو شده' WHERE user_id=? AND barber_id=? AND date=? AND time=?",
            (user_id, barber_id, date, time),
        )
        conn.commit()
        return True
    return False


# تابع برای نمایش نوبت‌های خالی به ادمین
def show_empty_appointments(chat_id):
    cursor.execute(
        "SELECT date, time, barber_id FROM appointments WHERE status='خالی' ORDER BY date, time"
    )
    appointments = cursor.fetchall()

    if appointments:
        table = "لیست نوبت‌های خالی:\n"
        for date, time, barber_id in appointments:
            cursor.execute("SELECT name FROM barbers WHERE id=?", (barber_id,))
            barber_name = cursor.fetchone()[0]
            table += f"{date} ساعت {time} - آرایشگر: {barber_name}\n"
        send_message(chat_id, table)
    else:
        send_message(chat_id, "هیچ نوبت خالی وجود ندارد.")


# تابع برای نمایش نوبت‌های رزرو شده به ادمین
def show_booked_appointments(chat_id):
    cursor.execute(
        "SELECT date, time, name, phone, service, payment_status, barber_id FROM appointments WHERE status='رزرو' ORDER BY date, time"
    )
    appointments = cursor.fetchall()

    if appointments:
        table = "لیست نوبت‌های رزرو شده:\n"
        for date, time, name, phone, service, payment_status, barber_id in appointments:
            cursor.execute("SELECT name FROM barbers WHERE id=?", (barber_id,))
            barber_name = cursor.fetchone()[0]
            service_fa = "اصلاح" if service == "service_haircut" else "خدمات VIP"
            table += f"{date} ساعت {time} - {name} ({phone}) - {service_fa} - {payment_status} - آرایشگر: {barber_name}\n"
        send_message(chat_id, table)
    else:
        send_message(chat_id, "هیچ نوبت رزرو شده‌ای وجود ندارد.")


# تابع برای ذخیره‌سازی اطلاعات کاربر و نوبت در جدول user_appointments
def save_user_appointment(user_id, barber_id, date, time, service, name, phone):
    cursor.execute(
        """
        INSERT INTO user_appointments (user_id, barber_id, date, time, service, name, phone)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, barber_id, date, time, service, name, phone),
    )
    conn.commit()


# تابع برای به‌روزرسانی وضعیت پرداخت در جدول user_appointments
def update_payment_status(user_id, barber_id, date, time, payment_status):
    cursor.execute(
        """
        UPDATE user_appointments
        SET payment_status=?
        WHERE user_id=? AND barber_id=? AND date=? AND time=?
        """,
        (payment_status, user_id, barber_id, date, time),
    )
    conn.commit()


# تابع برای دریافت اطلاعات نوبت کاربر از جدول user_appointments
def get_user_appointment(user_id):
    cursor.execute(
        """
        SELECT barber_id, date, time, service, name, phone, status, payment_status
        FROM user_appointments
        WHERE user_id=?
        """,
        (user_id,),
    )
    return cursor.fetchone()


# تابع اصلی
def main():
    update_appointments_table()
    last_update_id = 0

    while True:
        updates = get_updates(last_update_id + 1)
        if not updates["ok"] or not updates["result"]:
            continue

        for update in updates["result"]:
            last_update_id = update["update_id"]
            if "message" in update:
                handle_message(update["message"])
            elif "callback_query" in update:
                handle_callback_query(update["callback_query"])

        time.sleep(1)


def validate_phone_number(phone):
    # بررسی صحت شماره تماس با استفاده از regex
    return re.match(r"^09\d{9}$", phone) is not None


# تابع برای پردازش پیام‌های کاربر
def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text == "/start":
        user_id = message["from"]["id"]
        cursor.execute(
            "SELECT date, time FROM appointments WHERE user_id=? ORDER BY date, time LIMIT 1",
            (user_id,),
        )
        appointment = cursor.fetchone()

        if appointment:
            date, time = appointment
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "مشاهده نوبت من",
                            "callback_data": "show_my_appointment",
                        },
                        {"text": "لغو نوبت", "callback_data": "cancel_appointment"},
                    ],
                    [{"text": "نوبت جدید", "callback_data": "new_appointment"}],
                ]
            }
            send_message(
                chat_id,
                f"شما قبلاً نوبت گرفته‌اید. نوبت شما برای {date} ساعت {time} است.",
                reply_markup=keyboard,
            )
        else:
            keyboard = {
                "inline_keyboard": [
                    [{"text": "اصلاح", "callback_data": "service_haircut"}],
                    [{"text": "خدمات VIP", "callback_data": "service_vip"}],
                ]
            }
            send_message(
                chat_id, "لطفا نوع خدمت را انتخاب کنید:", reply_markup=keyboard
            )
    elif text == "/admin" and message["from"]["id"] == ADMIN_USER_ID:
        keyboard = {
            "inline_keyboard": [
                [{"text": "نمایش نوبت‌های خالی", "callback_data": "show_empty"}],
                [{"text": "نمایش نوبت‌های رزرو شده", "callback_data": "show_booked"}],
                [{"text": "بروزرسانی آرایشگرها", "callback_data": "update_barbers"}],
            ]
        }
        send_message(chat_id, "لطفا نوع نوبت‌ها را انتخاب کنید:", reply_markup=keyboard)
    elif text == "/update_barbers" and message["from"]["id"] == ADMIN_USER_ID:
        update_barbers_from_csv("barbers.csv")
        send_message(chat_id, "اطلاعات آرایشگرها با موفقیت بروزرسانی شد.")

    elif "awaiting_name" in context["user_data"]:
        context["user_data"]["name"] = text
        context["user_data"]["awaiting_phone"] = True
        del context["user_data"]["awaiting_name"]
        send_message(chat_id, "📞 لطفا شماره تماس خود را وارد کنید:")

    elif "awaiting_phone" in context["user_data"]:
        if validate_phone_number(text):
            user_id = message["from"]["id"]
            barber_id = context["user_data"].get("selected_barber_id")
            date = context["user_data"].get("selected_date")
            time = context["user_data"].get("selected_time")
            name = context["user_data"].get("name")
            phone = text

            if not (barber_id and date and time and name and phone):
                send_message(chat_id, "⚠️ خطا در ثبت نوبت. لطفا دوباره تلاش کنید.")
                return

            # به‌روزرسانی اطلاعات نوبت در دیتابیس
            cursor.execute(
                """
                UPDATE appointments
                SET user_id=?, name=?, phone=?, status='رزرو'
                WHERE date=? AND time=? AND barber_id=?
                """,
                (user_id, name, phone, date, time, barber_id),
            )
            conn.commit()

            # ذخیره در جدول user_appointments برای مدیریت بهتر
            cursor.execute(
                """
                INSERT INTO user_appointments (user_id, barber_id, date, time, service, name, phone, status, payment_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'رزرو', 'پرداخت نشده')
                """,
                (
                    user_id,
                    barber_id,
                    date,
                    time,
                    context["user_data"].get("service", "service_haircut"),
                    name,
                    phone,
                ),
            )
            conn.commit()

            # حذف اطلاعات موقت
            del context["user_data"]["awaiting_phone"]
            del context["user_data"]["selected_date"]
            del context["user_data"]["selected_time"]
            del context["user_data"]["selected_barber_id"]
            del context["user_data"]["name"]

            # ارسال پیام تایید و نمایش دکمه پرداخت
            keyboard = {
                "inline_keyboard": [
                    [{"text": "💳 پرداخت آنلاین", "callback_data": "pay_online"}],
                    [{"text": "💵 پرداخت حضوری", "callback_data": "pay_in_person"}],
                ]
            }
            send_message(
                chat_id,
                f"✅ نوبت شما برای {date} ساعت {time} ثبت شد.\n💈 لطفا روش پرداخت را انتخاب کنید:",
                reply_markup=keyboard,
            )
        else:
            send_message(
                chat_id,
                "❌ شماره تماس نامعتبر است. لطفا شماره صحیح وارد کنید (مثال: 09123456789).",
            )

    elif "awaiting_slot_selection" in context["user_data"]:
        try:
            slot_number = int(text)
            available_slots = context["user_data"]["available_slots"]
            if 1 <= slot_number <= len(available_slots):
                date_label, time = available_slots[slot_number - 1]
                context["user_data"]["selected_date"] = date_label
                context["user_data"]["selected_time"] = time
                send_message(chat_id, "لطفا نام خود را وارد کنید:")
                context["user_data"]["awaiting_name"] = True
                del context["user_data"]["awaiting_slot_selection"]
            else:
                send_message(chat_id, "شماره ردیف نامعتبر است. لطفا دوباره وارد کنید.")
        except ValueError:
            send_message(chat_id, "لطفا یک عدد وارد کنید.")


# تابع برای پردازش callback_query
def handle_callback_query(callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    data = callback_query["data"]
    user_id = callback_query["from"]["id"]

    if data == "start":
        handle_message(
            {"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/start"}
        )
        return

    if data == "service_haircut" or data == "service_vip":
        context["user_data"]["service"] = data
        show_barbers(chat_id)
    elif data.startswith("select_barber_"):
        barber_id = int(data.split("_")[2])  # استخراج barber_id از callback_data
        context["user_data"][
            "selected_barber_id"
        ] = barber_id  # ذخیره barber_id در context

        keyboard = {
            "inline_keyboard": [
                [{"text": "📅 اولین نوبت خالی", "callback_data": "first_available"}],
                [{"text": "📋 مشاهده جدول نوبت‌ها", "callback_data": "show_table"}],
            ]
        }

        send_message(
            chat_id,
            "🔽 لطفا یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=keyboard,
        )

    elif data == "first_available":
        barber_id = context["user_data"].get("selected_barber_id")
        if not barber_id:
            send_message(chat_id, "⚠️ لطفا ابتدا آرایشگر خود را انتخاب کنید.")
            return

        # بررسی نوبت‌های خالی در ۳ روز آینده با استفاده از filter_past_times
        for date_label, date_value in [
            ("امروز", to_jalali(datetime.now(pytz.timezone(USER_TIMEZONE)).date())),
            ("فردا", to_jalali((datetime.now(pytz.timezone(USER_TIMEZONE)) + timedelta(days=1)).date())),
            ("پس‌فردا", to_jalali((datetime.now(pytz.timezone(USER_TIMEZONE)) + timedelta(days=2)).date())),
        ]:
            available_times = filter_past_times(date_value)  # استفاده از تابع موجود برای فیلتر زمان‌های گذشته
            for time in available_times:
                cursor.execute(
                    "SELECT * FROM appointments WHERE date=? AND time=? AND status='خالی' AND barber_id=?",
                    (date_value, time, barber_id),
                )
                if cursor.fetchone():
                    context["user_data"]["selected_date"] = date_value
                    context["user_data"]["selected_time"] = time
                    keyboard = {
                        "inline_keyboard": [
                            [{"text": "✅ تایید", "callback_data": "confirm_first"}]
                        ]
                    }
                    send_message(
                        chat_id,
                        f"📅 اولین نوبت خالی: {date_label} ساعت {time}. آیا تایید می‌کنید؟",
                        reply_markup=keyboard,
                    )
                    return

        send_message(chat_id, "❌ نوبت خالی یافت نشد!")

    elif data == "confirm_first":
        user_id = callback_query["from"]["id"]
        barber_id = context["user_data"].get("selected_barber_id")
        date = context["user_data"].get("selected_date")
        time = context["user_data"].get("selected_time")

        if not (barber_id and date and time):
            send_message(chat_id, "⚠️ خطا در تایید نوبت. لطفا دوباره تلاش کنید.")
            return

        # ذخیره اطلاعات موقت برای ادامه روند
        context["user_data"]["awaiting_name"] = True

        send_message(chat_id, "👤 لطفا نام خود را وارد کنید:")

    elif data == "show_table":
        show_available_slots(chat_id, context["user_data"].get("selected_barber_id"))
    elif data == "confirm":
        if (
            "selected_date" in context["user_data"]
            and "selected_time" in context["user_data"]
        ):
            send_message(chat_id, "لطفا نام خود را وارد کنید:")
            context["user_data"]["awaiting_name"] = True
    elif data == "show_my_appointment":
        # دریافت همه نوبت‌های کاربر از جدول user_appointments
        cursor.execute(
            """
            SELECT date, time, service, barber_id, payment_status
            FROM user_appointments
            WHERE user_id=? AND status='رزرو'
            ORDER BY date, time
            """,
            (user_id,),
        )
        appointments = cursor.fetchall()

        if not appointments:
            send_message(chat_id, "شما هیچ نوبتی ندارید.")
        else:
            table = "📅 لیست نوبت‌های شما:\n"
            for date, time, service, barber_id, payment_status in appointments:
                # دریافت نام آرایشگر
                cursor.execute("SELECT name FROM barbers WHERE id=?", (barber_id,))
                barber = cursor.fetchone()
                barber_name = barber[0] if barber else "نامشخص"

                # تبدیل نوع سرویس به فارسی
                service_fa = "اصلاح" if service == "service_haircut" else "خدمات VIP"

                table += f"\n🕒 {date} ساعت {time}\n✂️ سرویس: {service_fa}\n💈 آرایشگر: {barber_name}\n💳 وضعیت پرداخت: {payment_status}\n"
                table += "------------------------"

            send_message(chat_id, table)

    elif data == "new_appointment":
        keyboard = {
            "inline_keyboard": [
                [{"text": "اصلاح", "callback_data": "service_haircut"}],
                [{"text": "خدمات VIP", "callback_data": "service_vip"}],
            ]
        }
        send_message(chat_id, "لطفا نوع خدمت را انتخاب کنید:", reply_markup=keyboard)
    elif data == "show_empty":
        show_empty_appointments(chat_id)
    elif data == "show_booked":
        show_booked_appointments(chat_id)
    elif data == "cancel_appointment":
        user_id = callback_query["from"]["id"]
        if cancel_appointment(user_id):
            send_message(chat_id, "نوبت شما با موفقیت لغو شد.")
        else:
            send_message(chat_id, "شما هیچ نوبتی برای لغو ندارید.")
    elif data == "pay_online":
        # دریافت اطلاعات نوبت کاربر از دیتابیس
        cursor.execute(
            "SELECT barber_id FROM user_appointments WHERE user_id=? AND status='رزرو'",
            (user_id,),
        )
        barber = cursor.fetchone()

        if not barber:
            send_message(chat_id, "خطا در دریافت اطلاعات آرایشگر.")
            return

        barber_id = barber[0]
        amount = 1800000
        description = "پرداخت هزینه خدمت آرایشگاه"
        invoice_response = send_invoice(chat_id, amount, description, barber_id)

        if invoice_response and invoice_response.get("ok"):
            send_message(chat_id, "لطفا پرداخت را از طریق فاکتور ارسال‌شده انجام دهید.")
        else:
            send_message(chat_id, "خطا در ارسال فاکتور پرداخت. لطفا دوباره تلاش کنید.")

    elif data == "pay_in_person":
        keyboard = {
            "inline_keyboard": [
                [{"text": "مشاهده نوبت من", "callback_data": "show_my_appointment"}],
                [{"text": "لغو نوبت", "callback_data": "cancel_appointment"}],
            ]
        }
        send_message(chat_id, "به صفحه اصلی بازگشتید:", reply_markup=keyboard)


if __name__ == "__main__":
    context = {"user_data": {}}
    main()
