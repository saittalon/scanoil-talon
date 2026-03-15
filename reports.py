from flask import Blueprint, send_file, render_template, request
from flask_login import login_required
from io import BytesIO
from calendar import monthrange
from datetime import date, datetime
import pandas as pd
from models import Client, Talon, Balance
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


def _talon_left_and_spent(t):
    left = 0.0 if t.effective_state == 'used' else float(t.liters or 0)
    spent = float(t.liters or 0) - left
    return left, spent


def _client_rows(talons, client):
    rows = []
    for t in talons:
        left, spent = _talon_left_and_spent(t)
        contract = t.contract
        price = contract.price_per_liter if (contract and contract.price_per_liter is not None) else 0.0
        rows.append({
            '№': t.serial_number,
            'Клиент': client.name,
            'Держатель': t.holder_name,
            'Договор': contract.number if contract else '',
            'Доп. соглашение': t.addendum_file.original_name if getattr(t, 'addendum_file', None) else '',
            'Товар': t.product_name,
            'Номинал': float(t.liters or 0),
            'Остаток': left,
            'Списано': spent,
            'С': t.valid_from.strftime('%d.%m.%Y') if t.valid_from else '',
            'По': t.valid_to.strftime('%d.%m.%Y') if t.valid_to else '',
            'Статус': talon_status_label(t),
            'Дата использования': format_kz(t.used_at, '%d.%m.%Y') if t.used_at else '',
            'Время использования': format_kz(t.used_at, '%H:%M') if t.used_at else '',
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
            'Талон': t.code,
            'Цена': float(price),
            'Стоимость': float(t.liters or 0) * float(price),
        })
    return rows


def _build_all_reports_data(talons):
    rows = []
    client_summary = {}
    for t in talons:
        contract = t.contract
        price = contract.price_per_liter if (contract and contract.price_per_liter is not None) else 0.0
        op_dt = t.used_at or t.created_at
        left, spent = _talon_left_and_spent(t)
        client_name = t.client.name if t.client else ''
        rows.append({
            'Дата': format_kz(op_dt, '%d.%m.%Y') if op_dt else '',
            'Время': format_kz(op_dt, '%H:%M:%S') if op_dt else '',
            'Владелец': t.holder_name,
            'Клиент': client_name,
            'Договор': contract.number if contract else '',
            'Доп. соглашение': t.addendum_file.original_name if getattr(t, 'addendum_file', None) else '',
            'Операция': 'Талон',
            'Услуга': t.product_name,
            'Количество': float(t.liters or 0),
            'Остаток': left,
            'Списано': spent,
            'Цена': float(price),
            'Стоимость': float(t.liters or 0) * float(price),
            'АЗС': t.used_agzs.name if t.used_agzs else '',
            'Адрес': '',
            'Статус': talon_status_label(t),
        })
        bucket = client_summary.setdefault(client_name, {
            'Талонов': 0, 'Всего литров': 0.0, 'Активные литры': 0.0, 'Использованные литры': 0.0,
            'Просроченные литры': 0.0, 'Заблокированные литры': 0.0, 'Сумма': 0.0
        })
        bucket['Талонов'] += 1
        bucket['Всего литров'] += float(t.liters or 0)
        bucket['Сумма'] += float(t.liters or 0) * float(price)
        state = t.effective_state
        if state == 'used':
            bucket['Использованные литры'] += float(t.liters or 0)
        elif state == 'expired':
            bucket['Просроченные литры'] += float(t.liters or 0)
        elif state == 'blocked':
            bucket['Заблокированные литры'] += float(t.liters or 0)
        else:
            bucket['Активные литры'] += float(t.liters or 0)
    summary_rows = [{'Клиент': name, **vals} for name, vals in client_summary.items()]
    balance_rows = [{
        'Клиент': b.client.name if b.client else '',
        'Договор': b.contract.number if b.contract else '—',
        'Товар': b.product_name,
        'Остаток': float(b.liters_left or 0),
        'Контроль': 'Да' if b.balance_control else 'Нет',
        'Обновлено': format_kz(b.updated_at) if b.updated_at else '',
    } for b in Balance.query.order_by(Balance.updated_at.desc()).all()]
    return rows, summary_rows, balance_rows


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
        df.to_excel(writer, index=False, sheet_name='client_report')
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
    rows, summary_rows, balance_rows = _build_all_reports_data(talons)
    total_balance_liters = sum(float(item['Остаток'] or 0) for item in balance_rows)

    total_count = len(talons)
    active_count = sum(1 for t in talons if t.effective_state == 'active')
    used_count = sum(1 for t in talons if t.effective_state == 'used')
    expired_count = sum(1 for t in talons if t.effective_state == 'expired')
    blocked_count = sum(1 for t in talons if t.effective_state == 'blocked')

    total_liters = sum(float(t.liters or 0) for t in talons)
    active_liters = sum(float(t.liters or 0) for t in talons if t.effective_state == 'active')
    used_liters = sum(float(t.liters or 0) for t in talons if t.effective_state == 'used')
    expired_liters = sum(float(t.liters or 0) for t in talons if t.effective_state == 'expired')
    blocked_liters = sum(float(t.liters or 0) for t in talons if t.effective_state == 'blocked')

    return render_template(
        'reports_all.html',
        rows=rows,
        summary_rows=summary_rows,
        balance_rows=balance_rows,
        total_balance_liters=total_balance_liters,
        total_count=total_count,
        active_count=active_count,
        used_count=used_count,
        expired_count=expired_count,
        blocked_count=blocked_count,
        total_liters=total_liters,
        active_liters=active_liters,
        used_liters=used_liters,
        expired_liters=expired_liters,
        blocked_liters=blocked_liters,
        date_from=date_from,
        date_to=date_to,
        selected_month=month,
    )


@reports_bp.get('/reports')
@login_required
def reports_index():
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template('reports_index.html', clients=clients)
