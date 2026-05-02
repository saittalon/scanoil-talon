import time
from datetime import datetime

from app import create_app
from mail_utils import send_monthly_reports, send_daily_report


print("SCHEDULER VERSION FINAL", flush=True)

app = create_app()

print("APP CREATED", flush=True)


# 🔒 защита от повторной отправки (в памяти)
last_run = {
    "daily": None,
    "monthly": None
}


def already_sent(key):
    now = datetime.now()

    if key == "daily":
        return last_run["daily"] == now.date()

    if key == "monthly":
        return last_run["monthly"] == (now.year, now.month)

    return False


def mark_sent(key):
    now = datetime.now()

    if key == "daily":
        last_run["daily"] = now.date()

    if key == "monthly":
        last_run["monthly"] = (now.year, now.month)


# =====================
# ⚙️ РЕЖИМ РАБОТЫ
# =====================

TEST_MODE = True   # 🔥 СМЕНИ НА False ПОСЛЕ ПРОВЕРКИ


def should_send_daily():
    if TEST_MODE:
        return True
    now = datetime.now()
    return now.hour == 18  # каждый день в 18:00


def should_send_monthly():
    if TEST_MODE:
        return True
    now = datetime.now()
    return now.day == 1 and now.hour == 9  # 1 числа в 09:00


# =====================
# 🚀 СТАРТ
# =====================

with app.app_context():
    print("SCHEDULER STARTED", flush=True)

    while True:
        try:
            now = datetime.now()
            print("NOW:", now, flush=True)

            # 📅 МЕСЯЧНЫЙ ОТЧЁТ
            print("CHECK MONTHLY...", flush=True)
            if should_send_monthly() and not already_sent("monthly"):
                print("SENDING MONTHLY REPORT...", flush=True)
                send_monthly_reports()
                mark_sent("monthly")

            # 📆 ЕЖЕДНЕВНЫЙ ОТЧЁТ
            print("CHECK DAILY...", flush=True)
            if should_send_daily() and not already_sent("daily"):
                print("SENDING DAILY REPORT...", flush=True)
                send_daily_report()
                mark_sent("daily")

        except Exception as e:
            print("SCHEDULER ERROR:", e, flush=True)

        time.sleep(60)  # проверка каждую минуту
            print("SCHEDULER ERROR:", e, flush=True)

        time.sleep(10)  # быстрее для теста
