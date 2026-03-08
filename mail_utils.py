import os, smtplib
from email.message import EmailMessage
from io import BytesIO
import pandas as pd
from datetime import datetime
from models import Client, Talon
from helpers import talon_status, format_kz_datetime


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
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        if username:
            s.login(username, password)
        s.send_message(msg)
    return True


def notify_event(subject: str, body: str):
    try:
        return send_email(subject, body)
    except Exception:
        return False


def daily_report_attachment():
    rows = []
    for t in Talon.query.order_by(Talon.created_at.desc()).all():
        rows.append({
            'Клиент': t.client.name if t.client else '',
            '№ талона': t.serial_number,
            'Код': t.code,
            'Литры': float(t.liters or 0),
            'Статус': talon_status(t),
            'Дата использования': format_kz_datetime(t.used_at) if t.used_at else '',
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
        })
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine='openpyxl') as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name='daily', index=False)
    bio.seek(0)
    return ('daily_report.xlsx', bio.read(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def send_daily_report():
    ts = datetime.now().strftime('%d.%m.%Y %H:%M')
    return send_email(
        f'Ежедневный отчет по талонам {ts}',
        'Во вложении ежедневный отчет по талонам и клиентам.',
        attachments=[daily_report_attachment()],
    )
