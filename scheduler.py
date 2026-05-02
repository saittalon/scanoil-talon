import os
import time
from datetime import datetime

from app import create_app
from mail_utils import send_monthly_reports, send_daily_report


app = create_app()

last_run_file = "/tmp/last_run.txt"


def already_sent(key):
    today = datetime.now().strftime("%Y-%m-%d") + "_" + key

    if os.path.exists(last_run_file):
        with open(last_run_file) as f:
            if today in f.read():
                return True

    with open(last_run_file, "a") as f:
        f.write(today + "\n")

    return False


def should_send_monthly():
    now = datetime.now()
    return now.day == 1 and now.hour == 9


def should_send_daily():
    now = datetime.now()
    return now.hour == 18  # каждый день в 18:00


with app.app_context():
    print("SCHEDULER STARTED")

    while True:
        try:
            # 📅 МЕСЯЧНЫЙ
            if should_send_monthly() and not already_sent("monthly"):
                print("SENDING MONTHLY REPORT...")
                send_monthly_reports()

            # 📆 ЕЖЕДНЕВНЫЙ
            if should_send_daily() and not already_sent("daily"):
                print("SENDING DAILY REPORT...")
                send_daily_report()

        except Exception as e:
            print("SCHEDULER ERROR:", e)

        time.sleep(60)
