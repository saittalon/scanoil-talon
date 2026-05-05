import os
import smtplib
import threading
from email.message import EmailMessage
from io import BytesIO
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd

from models import Talon
from helpers import format_kz, kz_now
from sqlalchemy.orm import joinedload


# ================= EMAIL =================

def _recipients():
    raw = os.getenv('MAIL_TO', '').strip()
    return [x.strip() for x in raw.split(',') if x.strip()]


def send_email(subject: str, body: str, attachments=None):
    recipients = _recipients()
    host = os.getenv('SMTP_HOST', '').strip()
    port = int(os.getenv('SMTP_PORT', '587'))
    username = os.getenv('SMTP_USERNAME', '').strip()
    password = os.getenv('SMTP_PASSWORD', '').strip()
    sender = os.getenv('MAIL_FROM', username or 'noreply@example.com')
    use_tls = os.getenv('SMTP_USE_TLS', '1').strip() not in ('0', 'false', 'False')

    print("\n=== EMAIL DEBUG ===")
    print("TO:", recipients)
    print("HOST:", host)
    print("===================")

    if not recipients:
        print("❌ MAIL_TO пустой")
        return False

    if not host:
        print("❌ SMTP_HOST пустой")
        return False

    try:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = ', '.join(recipients)
        msg.set_content(body)

        for name, content, mime in attachments or []:
            maintype, subtype = mime.split('/', 1)
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=name)

        print("CONNECTING SMTP...")

        with smtplib.SMTP(host, port, timeout=60) as s:
            if use_tls:
                s.starttls()
                print("TLS OK")

            if username:
                s.login(username, password)
                print("LOGIN OK")

            s.send_message(msg)
            print("✅ EMAIL SENT")

        return True

    except Exception as e:
        import traceback
        print("❌ EMAIL ERROR:", e)
        traceback.print_exc()
        return False


def notify_event(subject: str, body: str):
    def _worker():
        send_email(subject, body)

    threading.Thread(target=_worker, daemon=True).start()
    return True


# ================= QUERY =================

def get_used_talons_query(start=None, end=None):
    q = Talon.query.options(
        joinedload(Talon.addendum_file),
        joinedload(Talon.client),
        joinedload(Talon.contract),
        joinedload(Talon.used_agzs),
    ).filter(Talon.used_at.isnot(None))

    if start:
        q = q.filter(Talon.used_at >= start)

    # ❗ ВАЖНО: строго < end (чтобы не терялись записи)
    if end:
        q = q.filter(Talon.used_at < end)

    return q.order_by(Talon.used_at.desc())


# ================= EXCEL =================

def build_excel(rows, sheet_name="report"):
    bio = BytesIO()
    df = pd.DataFrame(rows)

    if df.empty:
        df = pd.DataFrame([{"Нет данных": "Нет данных за период"}])

    with pd.ExcelWriter(bio, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]

        ws.auto_filter.ref = ws.dimensions

        for col in ws.columns:
            max_length = 0
            col_letter = col[0].column_letter

            for cell in col:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))

            ws.column_dimensions[col_letter].width = max_length + 2

    bio.seek(0)
    return bio.read()


# ================= DAILY =================

def daily_report_attachment():
    now = kz_now()

    start = datetime(now.year, now.month, now.day)
    end = start + timedelta(days=1)

    rows = []

    for t in get_used_talons_query(start, end).all():
        if not t.client or not t.client.name:
            continue

        rows.append({
            'Клиент': t.client.name,
            '№ талона': t.serial_number,
            'Код талона': t.code,
            'Литры': float(t.liters or 0),
            'Дата': format_kz(t.used_at),
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
        })

    print("DAILY ROWS:", len(rows))

    file = build_excel(rows, "daily")

    return (
        'daily_report.xlsx',
        file,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


def send_daily_report():
    print("=== DAILY REPORT START ===")
    return send_email(
        subject="Ежедневный отчёт",
        body="Отчёт за сегодня",
        attachments=[daily_report_attachment()]
    )


# ================= MONTH RANGE =================

def get_last_month_range():
    now = kz_now()

    first_day_this_month = datetime(now.year, now.month, 1)

    if now.month == 1:
        start = datetime(now.year - 1, 12, 1)
    else:
        start = datetime(now.year, now.month - 1, 1)

    end = first_day_this_month  # ❗ строго < end

    print("MONTH RANGE:", start, end)

    return start, end


# ================= FINAL MONTHLY =================

def monthly_report_final():
    start, end = get_last_month_range()

    data = defaultdict(lambda: {"talons": 0, "liters": 0})

    talons = get_used_talons_query(start, end).all()

    print("TOTAL TALONS:", len(talons))

    for t in talons:
        if not t.client or not t.client.name:
            continue  # ❗ убираем сотрудников

        name = t.client.name.strip()

        data[name]["talons"] += 1
        data[name]["liters"] += float(t.liters or 0)

    rows = []

    for client, v in data.items():
        rows.append({
            "Контрагент": client,
            "Количество талонов": v["talons"],
            "Использовано литров": v["liters"]
        })

    print("CLIENTS COUNT:", len(rows))

    return build_excel(rows, "summary")


# ================= SEND MONTHLY =================

def send_monthly_reports():
    try:
        print("=== FINAL MONTHLY REPORT ===")

        file = monthly_report_final()

        return send_email(
            subject="Ежемесячный отчёт по контрагентам",
            body="Сводный отчёт за прошлый месяц",
            attachments=[(
                "monthly_summary.xlsx",
                file,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )]
        )

    except Exception as e:
        import traceback
        print("MONTHLY ERROR:")
        traceback.print_exc()
        return False
