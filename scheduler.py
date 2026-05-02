import time
from datetime import datetime

print("VERSION FINAL", flush=True)

from app import create_app
from mail_utils import send_monthly_reports, send_daily_report

print("IMPORTS OK", flush=True)

try:
    app = create_app()
    print("APP CREATED", flush=True)
except Exception as e:
    print("APP CREATE ERROR:", e, flush=True)
    raise


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


# 🔥 ВКЛЮЧАЕМ ТЕСТ
def should_send_daily():
    return True


def should_send_monthly():
    return True


with app.app_context():
    print("SCHEDULER STARTED", flush=True)

    while True:
        try:
            print("INSIDE LOOP", flush=True)

            now = datetime.now()
            print("NOW:", now, flush=True)

            if should_send_monthly() and not already_sent("monthly"):
                print("SENDING MONTHLY REPORT...", flush=True)
                send_monthly_reports()
                mark_sent("monthly")

            if should_send_daily() and not already_sent("daily"):
                print("SENDING DAILY REPORT...", flush=True)
                send_daily_report()
                mark_sent("daily")

        except Exception as e:
            print("SCHEDULER ERROR:", e, flush=True)

        time.sleep(10)  # быстрее для теста
