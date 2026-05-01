import os
import smtplib
import threading
from email.message import EmailMessage
from io import BytesIO
import pandas as pd
from models import Talon
from helpers import talon_status_label, format_kz


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
    if not recipients or not host:
        return False
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)
    msg.set_content(body)
    for name, content, mime in attachments or []:
        maintype, subtype = mime.split('/', 1)
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=name)
    with smtplib.SMTP(host, port, timeout=10) as s:
        if use_tls:
            s.starttls()
        if username:
            s.login(username, password)
        s.send_message(msg)
    return True


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


def daily_report_attachment():
    rows = []
    for t in Talon.query.order_by(Talon.created_at.desc()).all():
        rows.append({
            'Клиент': t.client.name if t.client else '',
            '№ талона': t.serial_number,
            'Код': t.code,
            'Литры': float(t.liters or 0),
            'Статус': talon_status_label(t),
            'Дата и время использования': format_kz(t.used_at),
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
        })
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine='openpyxl') as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name='daily', index=False)
    bio.seek(0)
    return ('daily_report.xlsx', bio.read(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
