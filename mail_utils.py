import os
import re
import smtplib
import threading
from email.message import EmailMessage
from io import BytesIO
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd
from sqlalchemy.orm import joinedload

from models import Talon
from helpers import format_kz, kz_now


def _recipients():
    raw = os.getenv('MAIL_TO', '').strip()
    return [x.strip() for x in raw.split(',') if x.strip()]


def _safe_filename(value: str) -> str:
    value = (value or "client").strip()
    value = re.sub(r'[\\/:*?"<>|]+', '_', value)
    value = re.sub(r'\s+', ' ', value)
    return value[:80] or "client"


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
    print("ATTACHMENTS:", len(attachments or []))
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

    return q.order_by(Talon.used_at.asc(), Talon.id.asc())


def _autosize_sheet(ws):
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"

    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter

        for cell in col:
            if cell.value is not None:
                max_length = max(max_length, len(str(cell.value)))

        ws.column_dimensions[col_letter].width = min(max_length + 3, 60)


def build_excel(rows, sheet_name="report"):
    bio = BytesIO()
    df = pd.DataFrame(rows)

    if df.empty:
        df = pd.DataFrame([{"Нет данных": "Нет данных за период"}])

    with pd.ExcelWriter(bio, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        _autosize_sheet(writer.sheets[sheet_name])

    bio.seek(0)
    return bio.read()


def build_monthly_summary_excel(summary_rows, detail_rows):
    bio = BytesIO()

    summary_df = pd.DataFrame(summary_rows)
    detail_df = pd.DataFrame(detail_rows)

    if summary_df.empty:
        summary_df = pd.DataFrame([{"Нет данных": "Нет использованных талонов за прошлый месяц"}])

    if detail_df.empty:
        detail_df = pd.DataFrame([{"Нет данных": "Нет использованных талонов за прошлый месяц"}])

    with pd.ExcelWriter(bio, engine='openpyxl') as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        detail_df.to_excel(writer, sheet_name="details", index=False)
        _autosize_sheet(writer.sheets["summary"])
        _autosize_sheet(writer.sheets["details"])

    bio.seek(0)
    return bio.read()


def build_client_monthly_excel(summary_rows, detail_rows):
    bio = BytesIO()

    summary_df = pd.DataFrame(summary_rows)
    detail_df = pd.DataFrame(detail_rows)

    if summary_df.empty:
        summary_df = pd.DataFrame([{"Нет данных": "Нет использованных талонов за прошлый месяц"}])

    if detail_df.empty:
        detail_df = pd.DataFrame([{"Нет данных": "Нет использованных талонов за прошлый месяц"}])

    with pd.ExcelWriter(bio, engine='openpyxl') as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        detail_df.to_excel(writer, sheet_name="details", index=False)
        _autosize_sheet(writer.sheets["summary"])
        _autosize_sheet(writer.sheets["details"])

    bio.seek(0)
    return bio.read()


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


def get_last_month_range():
    now = kz_now()

    first_day_this_month = datetime(now.year, now.month, 1)

    if now.month == 1:
        start = datetime(now.year - 1, 12, 1)
    else:
        start = datetime(now.year, now.month - 1, 1)

    end = first_day_this_month

    print("MONTH RANGE:", start, end)

    return start, end


def _monthly_rows():
    start, end = get_last_month_range()

    talons = get_used_talons_query(start, end).all()

    print("MONTHLY TOTAL USED TALONS:", len(talons))

    summary = defaultdict(lambda: {"talons": 0, "liters": 0.0})
    client_summary = defaultdict(lambda: defaultdict(lambda: {"talons": 0, "liters": 0.0}))
    detail_rows = []
    client_detail_rows = defaultdict(list)

    for t in talons:
        if not t.client or not t.client.name:
            continue

        client_name = t.client.name.strip()
        agzs_name = t.used_agzs.name if t.used_agzs else '—'
        liters = float(t.liters or 0)
        contract = t.contract

        key = (client_name, agzs_name)
        summary[key]["talons"] += 1
        summary[key]["liters"] += liters

        client_summary[client_name][agzs_name]["talons"] += 1
        client_summary[client_name][agzs_name]["liters"] += liters

        detail = {
            "Дата": format_kz(t.used_at, '%d.%m.%Y') if t.used_at else '',
            "Время": format_kz(t.used_at, '%H:%M:%S') if t.used_at else '',
            "Контрагент": client_name,
            "АГЗС": agzs_name,
            "Количество талонов": 1,
            "Использовано литров": liters,
            "№ талона": t.serial_number,
            "Код талона": t.code,
            "Договор": contract.number if contract else '',
            "Доп. соглашение": t.addendum_file.original_name if getattr(t, 'addendum_file', None) else '',
        }

        detail_rows.append(detail)
        client_detail_rows[client_name].append(detail)

    summary_rows = []
    for (client_name, agzs_name), v in sorted(summary.items(), key=lambda x: (x[0][0], x[0][1])):
        summary_rows.append({
            "Контрагент": client_name,
            "АГЗС": agzs_name,
            "Количество талонов": v["talons"],
            "Использовано литров": v["liters"],
        })

    client_files_data = {}
    for client_name, agzs_map in client_summary.items():
        rows = []
        for agzs_name, v in sorted(agzs_map.items(), key=lambda x: x[0]):
            rows.append({
                "Контрагент": client_name,
                "АГЗС": agzs_name,
                "Количество талонов": v["talons"],
                "Использовано литров": v["liters"],
            })

        client_files_data[client_name] = {
            "summary": rows,
            "details": client_detail_rows[client_name],
        }

    return summary_rows, detail_rows, client_files_data


def monthly_report_final():
    summary_rows, detail_rows, _client_files_data = _monthly_rows()
    return build_monthly_summary_excel(summary_rows, detail_rows)


def monthly_report_attachments():
    summary_rows, detail_rows, client_files_data = _monthly_rows()

    attachments = []

    summary_file = build_monthly_summary_excel(summary_rows, detail_rows)
    attachments.append((
        "monthly_summary.xlsx",
        summary_file,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ))

    for client_name, data in sorted(client_files_data.items(), key=lambda x: x[0]):
        file = build_client_monthly_excel(
            summary_rows=data["summary"],
            detail_rows=data["details"],
        )

        filename = f"{_safe_filename(client_name)}_monthly.xlsx"
        attachments.append((
            filename,
            file,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ))

    return attachments


def send_monthly_reports():
    try:
        print("=== MONTHLY REPORT START ===")

        attachments = monthly_report_attachments()

        return send_email(
            subject="Ежемесячный отчёт по контрагентам",
            body=(
                "Во вложении общий ежемесячный отчёт и отдельные Excel-файлы "
                "по каждому контрагенту. В отчётах добавлена разбивка по АГЗС."
            ),
            attachments=attachments
        )

    except Exception:
        import traceback
        print("MONTHLY ERROR:")
        traceback.print_exc()
        return False
