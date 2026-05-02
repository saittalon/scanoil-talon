import os
import time
from datetime import datetime

from app import create_app
from mail_utils import send_monthly_reports


app = create_app()

def should_send():
    now = datetime.now()
    return now.day == 1 and now.hour == 9  # 1 число 09:00


with app.app_context():
    print("SCHEDULER STARTED")

    while True:
        try:
            if should_send():
                print("SENDING MONTHLY REPORT...")
                send_monthly_reports()

                # чтобы не отправляло 100 раз
                time.sleep(3600)

        except Exception as e:
            print("SCHEDULER ERROR:", e)

        time.sleep(60)
