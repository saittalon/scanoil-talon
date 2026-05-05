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
        try:
            send_email(subject, body)
        except Exception as e:
            print(f'EMAIL ERROR: {e}')

    try:
        threading.Thread(target=_worker, daemon=True).start()
        return True
    except Exception as e:
        print(f'EMAIL THREAD ERROR: {e}')
        return False


# ================= DATA =================

from sqlalchemy.orm import joinedload

def get_used_talons_query(start=None, end=None):
    q = Talon.query.options(
        joinedload(Talon.addendum_file)
    ).filter(Talon.used_at.isnot(None))

    if start:
        q = q.filter(Talon.used_at >= start)
    if end:
        q = q.filter(Talon.used_at <= end)

    return q.order_by(Talon.used_at.desc())


# ================= EXCEL =================

def build_excel(rows, sheet_name="report"):
    bio = BytesIO()
    df = pd.DataFrame(rows)

    if df.empty:
        df = pd.DataFrame([
            {"Нет данных": "За выбранный период нет использованных талонов"}
        ])

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
        rows.append({
            'Клиент': t.client.name if t.client else '',
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
    try:
        print("=== DAILY REPORT START ===")
        return send_email(
            subject="Ежедневный отчёт (использованные талоны)",
            body="Отчёт за сегодня",
            attachments=[daily_report_attachment()]
        )
    except Exception as e:
        import traceback
        print("DAILY REPORT ERROR:")
        traceback.print_exc()
        return False


# ================= MONTHLY =================

def get_last_month_range():
    now = kz_now()

    first_day_this_month = datetime(now.year, now.month, 1)
    last_month_end = first_day_this_month - timedelta(seconds=1)
    last_month_start = datetime(last_month_end.year, last_month_end.month, 1)

    # 🔥 фикс диапазона (без потерь)
    start = last_month_start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = last_month_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    print("MONTH RANGE:", start, end)

    return start, end


def monthly_report_attachment():
    start, end = get_last_month_range()

    rows = []

    for t in get_used_talons_query(start, end).all():
        rows.append({
            'Клиент': t.client.name if t.client else '',
            '№ талона': t.serial_number,
            'Код талона': t.code,
            'Литры': float(t.liters or 0),
            'Дата': format_kz(t.used_at),
            'Договор': t.contract.number if t.contract else '',
            'Доп. соглашение': getattr(t.addendum_file, "original_name", ""),
        })

    print("MONTHLY ROWS:", len(rows))

    file = build_excel(rows, "monthly_all")

    return (
        'monthly_report.xlsx',
        file,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


def monthly_reports_by_clients():
    start, end = get_last_month_range()

    data = defaultdict(list)

    for t in get_used_talons_query(start, end).all():
        # 🔥 нормализация клиента
        client = (
            t.client.name.strip().lower()
            if t.client and t.client.name
            else f"без клиента #{t.id}"
        )

        data[client].append({
            '№ талона': t.serial_number,
            'Код талона': t.code,
            'Литры': float(t.liters or 0),
            'Дата': format_kz(t.used_at),
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
            'Договор': t.contract.number if t.contract else '',
            'Доп. соглашение': getattr(t.addendum_file, "original_name", ""),
        })

    attachments = []

    for client, rows in data.items():
        file = build_excel(rows, client[:20])

        attachments.append((
            f"{client}_monthly.xlsx",
            file,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ))

    print("CLIENT FILES:", len(attachments))

    return attachments


def send_monthly_reports():
    try:
        print("=== MONTHLY REPORT START ===")
        attachments = [monthly_report_attachment()]
        attachments += monthly_reports_by_clients()

        return send_email(
            subject="Ежемесячные отчёты",
            body="Общий + по каждому клиенту",
            attachments=attachments
        )
    except Exception as e:
        import traceback
        print("MONTHLY REPORT ERROR:")
        traceback.print_exc()
        return False
