import os
import smtplib
import threading
from email.message import EmailMessage
from io import BytesIO
from datetime import timedelta

import pandas as pd
from sqlalchemy.orm import joinedload

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

        with smtplib.SMTP(host, port, timeout=60) as s:
            if use_tls:
                s.starttls()

            if username:
                s.login(username, password)

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
    ).filter(
        Talon.used_at.isnot(None),
        Talon.state == 'used'
    )

    if start:
        q = q.filter(Talon.used_at >= start)

    if end:
        q = q.filter(Talon.used_at < end)

    return q.order_by(Talon.used_at.asc())


# ================= EXCEL =================

def build_excel(rows, sheet_name="daily"):
    bio = BytesIO()
    df = pd.DataFrame(rows)

    if df.empty:
        df = pd.DataFrame([{"Нет данных": "Нет использованных талонов за период"}])

    with pd.ExcelWriter(bio, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]

        ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = "A2"

        for col in ws.columns:
            max_length = 0
            col_letter = col[0].column_letter

            for cell in col:
                if cell.value is not None:
                    max_length = max(max_length, len(str(cell.value)))

            ws.column_dimensions[col_letter].width = max_length + 3

    bio.seek(0)
    return bio.read()


# ================= DAILY REPORT =================

def daily_report_attachment():
    now = kz_now()

    # Письмо отправляется в 01:00 ночи, поэтому берём ВЧЕРАШНИЙ день
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today_start - timedelta(days=1)
    end = today_start

    rows = []

    talons = get_used_talons_query(start, end).all()

    for t in talons:
        contract = t.contract

        rows.append({
            'Клиент': t.client.name if t.client else '',
            'Дата': format_kz(t.used_at, '%d.%m.%Y') if t.used_at else '',
            'Время': format_kz(t.used_at, '%H:%M:%S') if t.used_at else '',
            '№ талона': t.serial_number,
            'Код талона': t.code,
            'Договор': contract.number if contract else '',
            'Доп. соглашение': t.addendum_file.original_name if getattr(t, 'addendum_file', None) else '',
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
            'Литры': float(t.liters or 0),
        })

    print("DAILY USED ONLY ROWS:", len(rows))
    print("DAILY PERIOD:", start, end)

    file = build_excel(rows, "daily")

    return (
        'daily_report.xlsx',
        file,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


def send_daily_report():
    now = kz_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    report_day = today_start - timedelta(days=1)

    return send_email(
        subject='Ежедневный отчёт по использованным талонам',
        body=f'Во вложении отчёт по использованным талонам за {report_day.strftime("%d.%m.%Y")}.',
        attachments=[daily_report_attachment()],
    )
