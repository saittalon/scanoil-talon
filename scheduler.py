import time

from app import create_app
from mail_utils import send_monthly_reports, send_daily_report
from helpers import kz_now

print("SCHEDULER FINAL", flush=True)

app = create_app()

print("APP CREATED", flush=True)


# 🔒 защита от повторной отправки (в памяти)
last_run = {
    "daily": None,
    "monthly": None
}


def already_sent(key):
    now = kz_now()

    if key == "daily":
        return last_run["daily"] == now.date()

    if key == "monthly":
        return last_run["monthly"] == (now.year, now.month)

    return False


def mark_sent(key):
    now = kz_now()

    if key == "daily":
        last_run["daily"] = now.date()

    if key == "monthly":
        last_run["monthly"] = (now.year, now.month)


# =====================
# 📅 УСЛОВИЯ ОТПРАВКИ
# =====================

def should_send_daily(now):
    return now.hour == 18  # каждый день в 18:00


def should_send_monthly(now):
    return now.day == 1 and now.hour == 9  # 1 числа в 09:00


# =====================
# 🚀 СТАРТ
# =====================

with app.app_context():
    print("SCHEDULER STARTED", flush=True)

    while True:
        try:
            now = kz_now()
            print("NOW:", now, flush=True)

            # 📅 ЕЖЕМЕСЯЧНЫЙ
            print("CHECK MONTHLY...", flush=True)
            if should_send_monthly(now) and not already_sent("monthly"):
                print("CALLING MONTHLY...", flush=True)
                result = send_monthly_reports()
                print("MONTHLY RESULT:", result, flush=True)
                mark_sent("monthly")

            # 📆 ЕЖЕДНЕВНЫЙ
            print("CHECK DAILY...", flush=True)
            if should_send_daily(now) and not already_sent("daily"):
                print("CALLING DAILY...", flush=True)
                result = send_daily_report()
                print("DAILY RESULT:", result, flush=True)
                mark_sent("daily")

        except Exception as e:
            import traceback
            print("SCHEDULER ERROR:", e, flush=True)
            traceback.print_exc()

        time.sleep(60)
