import time
from datetime import datetime

from app import create_app
from mail_utils import send_monthly_reports, send_daily_report


print("VERSION FINAL", flush=True)

app = create_app()

print("APP CREATED", flush=True)


# 🔒 защита от дублей (в памяти)
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


def should_send_daily():
    return True


def should_send_monthly():
    now = datetime.now()
    return now.day == 1 and now.hour == 9  # 1 числа в 09:00


with app.app_context():
    print("SCHEDULER STARTED", flush=True)

    while True:
        try:
            now = datetime.now()
            print("NOW:", now, flush=True)

            # 📅 МЕСЯЧНЫЙ ОТЧЁТ
            if should_send_monthly() and not already_sent("monthly"):
                print("SENDING MONTHLY REPORT...", flush=True)
                send_monthly_reports()
                mark_sent("monthly")

            # 📆 ЕЖЕДНЕВНЫЙ ОТЧЁТ
            if should_send_daily() and not already_sent("daily"):
                print("SENDING DAILY REPORT...", flush=True)
                send_daily_report()
                mark_sent("daily")

        except Exception as e:
            print("SCHEDULER ERROR:", e, flush=True)

        time.sleep(60)
