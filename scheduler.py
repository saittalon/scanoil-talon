print("VERSION 2", flush=True)

import os
import time
from datetime import datetime

from app import create_app
from mail_utils import send_monthly_reports, send_daily_report


app = create_app()

print("FILE LOADED", flush=True)


last_run_file = "/tmp/last_run.txt"


def already_sent(key):
    now = datetime.now()
    marker = now.strftime("%Y-%m-%d") + "_" + key

    if os.path.exists(last_run_file):
        with open(last_run_file, "r") as f:
            if marker in f.read():
                return True

    with open(last_run_file, "a") as f:
        f.write(marker + "\n")

    return False


def should_send_monthly():
    now = datetime.now()
    return now.day == 1 and now.hour == 9


def should_send_daily():
    now = datetime.now()
    return now.hour == 18


with app.app_context():
    print("SCHEDULER STARTED", flush=True)

    while True:
        try:
            now = datetime.now()
            print("NOW:", now, flush=True)

            # 📅 МЕСЯЧНЫЙ
            if should_send_monthly() and not already_sent("monthly"):
                print("SENDING MONTHLY REPORT...", flush=True)
                send_monthly_reports()

            # 📆 ЕЖЕДНЕВНЫЙ
            if should_send_daily() and not already_sent("daily"):
                print("SENDING DAILY REPORT...", flush=True)
                send_daily_report()

        except Exception as e:
            print("SCHEDULER ERROR:", e, flush=True)

        time.sleep(60)
