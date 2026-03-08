from flask import Blueprint, send_file, render_template, request
from flask_login import login_required
from io import BytesIO
from calendar import monthrange
from datetime import date, datetime
import pandas as pd
from models import Client, Talon
from helpers import talon_status_label, format_kz

reports_bp = Blueprint('reports', __name__)

MONTH_NAMES = [
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
]


def _month_bounds(year: int, month: int):
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _subtract_months(base_date: date, months_back: int) -> date:
    y = base_date.year
    m = base_date.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def dashboard_month_links(today=None):
    today = today or date.today()
    prev1 = _subtract_months(today.replace(day=1), 1)
    prev2 = _subtract_months(today.replace(day=1), 2)
    return [
        {
            'title': MONTH_NAMES[prev1.month - 1],
            'label': f'Отчёт по всем клиентам за {MONTH_NAMES[prev1.month - 1]}',
            'href': f"/reports/all.xlsx?month={prev1.year}-{prev1.month:02d}",
        },
        {
            'title': MONTH_NAMES[prev2.month - 1],
            'label': f'Отчёт по всем клиентам за {MONTH_NAMES[prev2.month - 1]}',
            'href': f"/reports/all.xlsx?month={prev2.year}-{prev2.month:02d}",
        },
    ]


def _resolve_period():
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    month = (request.args.get('month') or '').strip()

    if month:
        try:
            year, month_num = month.split('-', 1)
            start, end = _month_bounds(int(year), int(month_num))
            return start, end, start.isoformat(), end.isoformat(), month
        except Exception:
            pass

    start = None
    end = None
    if date_from:
        try:
            start = pd.to_datetime(date_from).date()
        except Exception:
            date_from = ''
    if date_to:
        try:
            end = pd.to_datetime(date_to).date()
        except Exception:
            date_to = ''
    return start, end, date_from, date_to, ''


def _filter_talons(q, start, end):
    if start:
        q = q.filter(Talon.created_at >= datetime.combine(start, datetime.min.time()))
    if end:
        q = q.filter(Talon.created_at <= datetime.combine(end, datetime.max.time()))
    return q


def _client_rows(talons, client):
    rows = []
    for t in talons:
        rows.append({
            '№': t.serial_number,
            'Клиент': client.name,
            'Держатель': t.holder_name,
            'Товар': t.product_name,
            'Номинал': float(t.liters or 0),
            'С': t.valid_from.strftime('%d.%m.%Y') if t.valid_from else '',
            'По': t.valid_to.strftime('%d.%m.%Y') if t.valid_to else '',
            'Статус': talon_status_label(t),
            'Дата использования': format_kz(t.used_at, '%d.%m.%Y') if t.used_at else '',
            'Время использования': format_kz(t.used_at, '%H:%M') if t.used_at else '',
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
            'Доп. соглашение': t.addendum_file.original_name if getattr(t, 'addendum_file', None) else '',
            'Талон': t.code,
        })
    return rows


@reports_bp.get('/clients/<int:client_id>/report.xlsx')
@login_required
def client_report_excel(client_id: int):
    client = Client.query.get_or_404(client_id)
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    q = Talon.query.filter_by(client_id=client.id)
    if date_from:
        try:
            q = q.filter(Talon.valid_from >= pd.to_datetime(date_from).date())
        except Exception:
            pass
    if date_to:
        try:
            q = q.filter(Talon.valid_to <= pd.to_datetime(date_to).date())
        except Exception:
            pass

    talons = q.order_by(Talon.id.asc()).all()
    df = pd.DataFrame(_client_rows(talons, client))
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='clients_coupons')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f'report_{client.name}.xlsx'.replace(' ', '_'), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@reports_bp.get('/reports/all.xlsx')
@login_required
def all_clients_report_excel():
    start, end, date_from, date_to, month = _resolve_period()
    q = _filter_talons(Talon.query, start, end)
    talons = q.order_by(Talon.created_at.desc()).all()
    rows = []
    for t in talons:
        contract = t.contract
        price = contract.price_per_liter if (contract and contract.price_per_liter is not None) else 0.0
        rows.append({
            'Клиент': t.client.name if t.client else '',
            'Договор': contract.number if contract else '',
            '№ талона': t.serial_number,
            'Код': t.code,
            'Статус': talon_status_label(t),
            'Дата использования': format_kz(t.used_at, '%d.%m.%Y') if t.used_at else '',
            'Время использования': format_kz(t.used_at, '%H:%M') if t.used_at else '',
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
            'Услуга': t.product_name,
            'Количество': float(t.liters or 0),
            'Цена': float(price),
            'Стоимость': float(t.liters or 0) * float(price),
            'Доп. соглашение': t.addendum_file.original_name if getattr(t, 'addendum_file', None) else '',
        })
    df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='scanoilcard_report')
    output.seek(0)
    suffix = month or (f'{date_from}_{date_to}' if date_from or date_to else 'all')
    suffix = suffix.replace(':', '-').replace('/', '-')
    return send_file(output, as_attachment=True, download_name=f'allclients_report_{suffix}.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@reports_bp.get('/reports/all')
@login_required
def reports_all_page():
    start, end, date_from, date_to, month = _resolve_period()
    q = _filter_talons(Talon.query, start, end)
    talons = q.order_by(Talon.created_at.desc()).all()
    rows = []
    for t in talons:
        price = t.contract.price_per_liter if (t.contract and t.contract.price_per_liter is not None) else 0.0
        op_dt = t.used_at or t.created_at
        rows.append({
            'Дата': format_kz(op_dt, '%d.%m.%Y') if op_dt else '',
            'Время': format_kz(op_dt, '%H:%M:%S') if op_dt else '',
            'Владелец': t.holder_name,
            'Клиент': t.client.name if t.client else '',
            'Операция': 'Талон',
            'Услуга': t.product_name,
            'Количество': float(t.liters or 0),
            'Цена': float(price),
            'Стоимость': float(t.liters or 0) * float(price),
            'АЗС': t.used_agzs.name if t.used_agzs else '',
            'Адрес': '',
            'Статус': talon_status_label(t),
        })
    return render_template('reports_all.html', rows=rows, date_from=date_from, date_to=date_to, selected_month=month)


@reports_bp.get('/reports')
@login_required
def reports_index():
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template('reports_index.html', clients=clients)
