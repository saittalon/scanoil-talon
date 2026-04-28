from flask import Blueprint, send_file, render_template, request
from flask_login import login_required
from io import BytesIO
from calendar import monthrange
from datetime import date, datetime
import pandas as pd
from models import Client, Talon, Balance, Shift, AGZS
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





def _parse_date_arg(value: str):
    value = (value or '').strip()
    if not value:
        return None, ''
    try:
        return pd.to_datetime(value).date(), value
    except Exception:
        return None, ''

def _talon_operation_dt(t):
    return t.used_at or t.created_at


def _is_in_period(dt_value, start, end):
    if dt_value is None:
        return not start and not end
    dt_date = dt_value.date() if hasattr(dt_value, 'date') else dt_value
    if start and dt_date < start:
        return False
    if end and dt_date > end:
        return False
    return True


def _filter_talons(q, start, end):
    talons = q.all()
    if not start and not end:
        return talons
    return [t for t in talons if _is_in_period(_talon_operation_dt(t), start, end)]


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


def _resolved_client_category(client):
    if not client:
        return 'employee'
    raw = (getattr(client, 'category', None) or '').strip().lower()
    if raw in ('counterparty', 'employee'):
        return raw
    sample = f"{getattr(client, 'name', '') or ''} {getattr(client, 'full_name', '') or ''}".lower()
    if any(marker in sample for marker in ['тоо', 'too', 'ип', 'ip', 'llp']):
        return 'counterparty'
    return 'employee'


def _selected_category():
    value = (request.args.get('category') or '').strip().lower()
    return value if value in ('counterparty', 'employee') else ''


def _category_label(value: str):
    return {
        'counterparty': 'Контрагенты',
        'employee': 'Сотрудники',
    }.get(value, 'Все')


def _filter_talons_by_category(talons, category: str):
    if not category:
        return talons
    return [t for t in talons if _resolved_client_category(getattr(t, 'client', None)) == category]


def _balance_rows_for_category(category: str):
    rows = []
    balances = Balance.query.order_by(Balance.updated_at.desc()).all()
    for b in balances:
        client = getattr(b, 'client', None)
        if category and _resolved_client_category(client) != category:
            continue
        rows.append({
            'Клиент': client.name if client else '',
            'Договор': b.contract.number if b.contract else '—',
            'Товар': b.product_name,
            'Остаток': float(b.liters_left or 0),
            'Контроль': 'Да' if b.balance_control else 'Нет',
            'Обновлено': format_kz(b.updated_at) if b.updated_at else '',
        })
    return rows


def _build_all_reports_data(talons, category: str = ''):
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
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
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
    balance_rows = _balance_rows_for_category(category)
    return rows, summary_rows, balance_rows


@reports_bp.get('/clients/<int:client_id>/report.xlsx')
@login_required
def client_report_excel(client_id: int):
    client = Client.query.get_or_404(client_id)
    category = _selected_category() or _resolved_client_category(client)
    category_label = _category_label(category)

    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()

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

    all_client_talons = Talon.query.filter_by(client_id=client.id).order_by(Talon.created_at.desc(), Talon.id.desc()).all()
    talons = [t for t in all_client_talons if _is_in_period(_talon_operation_dt(t), start, end)]
    balances = Balance.query.filter_by(client_id=client.id).order_by(Balance.updated_at.desc()).all()

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
    balance_liters = sum(float(b.liters_left or 0) for b in balances)

    detail_rows = _client_rows(talons, client)
    detail_rows = sorted(detail_rows, key=lambda r: ((r.get('Доп. соглашение') or '—'), str(r.get('№') or '')))

    total_sum = sum(float(row.get('Стоимость') or 0) for row in detail_rows)

    addendum_summary = {}
    for row in detail_rows:
        addendum = row.get('Доп. соглашение') or '— без доп. соглашения —'
        bucket = addendum_summary.setdefault(addendum, {
            'Доп. соглашение': addendum,
            'Талонов': 0,
            'Всего литров': 0.0,
            'Остаток литров': 0.0,
            'Списано литров': 0.0,
            'Сумма': 0.0,
        })
        bucket['Талонов'] += 1
        bucket['Всего литров'] += float(row.get('Номинал') or 0)
        bucket['Остаток литров'] += float(row.get('Остаток') or 0)
        bucket['Списано литров'] += float(row.get('Списано') or 0)
        bucket['Сумма'] += float(row.get('Стоимость') or 0)

    balance_rows = [{
        'Договор': b.contract.number if b.contract else '—',
        'Товар': b.product_name,
        'Остаток': float(b.liters_left or 0),
        'Контроль': 'Да' if b.balance_control else 'Нет',
        'Обновлено': format_kz(b.updated_at, '%d.%m.%Y %H:%M') if b.updated_at else '',
    } for b in balances]

    summary_client_df = pd.DataFrame([{
        'Клиент': client.name,
        'Талонов': total_count,
        'Всего литров': total_liters,
        'Активные литры': active_liters,
        'Использованные литры': used_liters,
        'Просроченные литры': expired_liters,
        'Заблокированные литры': blocked_liters,
        'Сумма': total_sum,
    }])

    summary_df = pd.DataFrame([
        {'Показатель': 'Клиент', 'Значение': client.name},
        {'Показатель': 'Период (от)', 'Значение': date_from or '—'},
        {'Показатель': 'Период (до)', 'Значение': date_to or '—'},
        {'Показатель': 'Категория', 'Значение': category_label},
        {'Показатель': 'Всего талонов', 'Значение': total_count},
        {'Показатель': 'Активные талоны', 'Значение': active_count},
        {'Показатель': 'Использованные талоны', 'Значение': used_count},
        {'Показатель': 'Просроченные талоны', 'Значение': expired_count},
        {'Показатель': 'Заблокированные талоны', 'Значение': blocked_count},
        {'Показатель': 'Всего литров', 'Значение': total_liters},
        {'Показатель': 'Активный объём, л', 'Значение': active_liters},
        {'Показатель': 'Использовано литров', 'Значение': used_liters},
        {'Показатель': 'Просрочено литров', 'Значение': expired_liters},
        {'Показатель': 'Заблокировано литров', 'Значение': blocked_liters},
        {'Показатель': 'Осталось газа по договорам, л', 'Значение': balance_liters},
        {'Показатель': 'Общая сумма', 'Значение': total_sum},
    ])
    addendum_df = pd.DataFrame(sorted(addendum_summary.values(), key=lambda r: str(r.get('Доп. соглашение') or '')))
    balance_df = pd.DataFrame(balance_rows)
    detail_df = pd.DataFrame(detail_rows)

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        sheet_name = 'client_report'
        summary_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=1)

        client_start = len(summary_df) + 5
        summary_client_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=client_start)

        addendum_start = client_start + len(summary_client_df) + 4
        addendum_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=addendum_start)

        balance_start = addendum_start + len(addendum_df) + 4
        balance_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=balance_start)

        detail_start = balance_start + len(balance_df) + 4
        detail_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=detail_start)

        ws = writer.sheets[sheet_name]
        ws['A1'] = 'Сводка по клиенту'
        ws[f'A{client_start + 1}'] = 'Сводка по клиентам'
        ws[f'A{addendum_start + 1}'] = 'Сводка по доп. соглашениям'
        ws[f'A{balance_start + 1}'] = 'Остатки по договорам'
        ws[f'A{detail_start + 1}'] = 'Детальный отчёт по талонам'

        widths = {
            'A': 16, 'B': 20, 'C': 18, 'D': 16, 'E': 24, 'F': 14, 'G': 14, 'H': 14,
            'I': 12, 'J': 12, 'K': 14, 'L': 18, 'M': 18, 'N': 14, 'O': 16, 'P': 20,
            'Q': 12, 'R': 14,
        }
        for col, width in widths.items():
            ws.column_dimensions[col].width = width
        ws.freeze_panes = f'A{detail_start + 2}'

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=f'report_{client.name}.xlsx'.replace(' ', '_'),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    
@reports_bp.get('/reports/all.xlsx')
@login_required
def all_clients_report_excel():
    start, end, date_from, date_to, month = _resolve_period()
    category = _selected_category()
    category_label = _category_label(category)
    talons = _filter_talons(Talon.query.order_by(Talon.id.asc()), start, end)
    talons = _filter_talons_by_category(talons, category)
    balances = Balance.query.order_by(Balance.updated_at.desc()).all()
    if category:
        balances = [b for b in balances if _resolved_client_category(getattr(b, 'client', None)) == category]

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
    balance_liters = sum(float(b.liters_left or 0) for b in balances)

    detail_rows = []
    client_summary = {}
    addendum_summary = {}

    for t in talons:
        contract = t.contract
        client_name = t.client.name if t.client else ''
        holder_name = t.holder_name or client_name
        price = float(contract.price_per_liter or 0) if (contract and contract.price_per_liter is not None) else 0.0
        addendum = t.addendum_file.original_name if getattr(t, 'addendum_file', None) else '— без доп. соглашения —'
        left, spent = _talon_left_and_spent(t)
        cost = float(t.liters or 0) * price

        detail_rows.append({
            '№': t.serial_number,
            'Клиент': client_name,
            'Держатель': holder_name,
            'Договор': contract.number if contract else '',
            'Доп. соглашение': addendum,
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
            'Цена': price,
            'Стоимость': cost,
        })

        c_bucket = client_summary.setdefault(client_name, {
            'Клиент': client_name,
            'Талонов': 0,
            'Всего литров': 0.0,
            'Активные литры': 0.0,
            'Использованные литры': 0.0,
            'Просроченные литры': 0.0,
            'Заблокировано': 0.0,
            'Сумма': 0.0,
        })
        c_bucket['Талонов'] += 1
        c_bucket['Всего литров'] += float(t.liters or 0)
        c_bucket['Сумма'] += cost
        if t.effective_state == 'used':
            c_bucket['Использованные литры'] += float(t.liters or 0)
        elif t.effective_state == 'expired':
            c_bucket['Просроченные литры'] += float(t.liters or 0)
        elif t.effective_state == 'blocked':
            c_bucket['Заблокировано'] += float(t.liters or 0)
        else:
            c_bucket['Активные литры'] += float(t.liters or 0)

        a_bucket = addendum_summary.setdefault(addendum, {
            'Доп. соглашение': addendum,
            'Талонов': 0,
            'Всего литров': 0.0,
            'Остаток литров': 0.0,
            'Списано литров': 0.0,
            'Сумма': 0.0,
        })
        a_bucket['Талонов'] += 1
        a_bucket['Всего литров'] += float(t.liters or 0)
        a_bucket['Остаток литров'] += left
        a_bucket['Списано литров'] += spent
        a_bucket['Сумма'] += cost

    detail_rows = sorted(detail_rows, key=lambda r: (str(r.get('Клиент') or ''), str(r.get('Доп. соглашение') or ''), str(r.get('№') or '')))
    summary_df = pd.DataFrame([
        {'Показатель': 'Период (от)', 'Значение': date_from or '—'},
        {'Показатель': 'Период (до)', 'Значение': date_to or '—'},
        {'Показатель': 'Категория', 'Значение': category_label},
        {'Показатель': 'Всего талонов', 'Значение': total_count},
        {'Показатель': 'Активные талоны', 'Значение': active_count},
        {'Показатель': 'Использованные талоны', 'Значение': used_count},
        {'Показатель': 'Просроченные талоны', 'Значение': expired_count},
        {'Показатель': 'Заблокированные талоны', 'Значение': blocked_count},
        {'Показатель': 'Всего литров', 'Значение': total_liters},
        {'Показатель': 'Активный объём, л', 'Значение': active_liters},
        {'Показатель': 'Использовано литров', 'Значение': used_liters},
        {'Показатель': 'Просрочено литров', 'Значение': expired_liters},
        {'Показатель': 'Заблокировано литров', 'Значение': blocked_liters},
        {'Показатель': 'Клиентов в отчёте', 'Значение': len(client_summary)},
        {'Показатель': 'Осталось газа по договорам, л', 'Значение': balance_liters},
    ])
    client_summary_df = pd.DataFrame(sorted(client_summary.values(), key=lambda r: str(r.get('Клиент') or '')))
    addendum_df = pd.DataFrame(sorted(addendum_summary.values(), key=lambda r: str(r.get('Доп. соглашение') or '')))
    balance_df = pd.DataFrame([{
        'Клиент': b.client.name if b.client else '',
        'Договор': b.contract.number if b.contract else '—',
        'Товар': b.product_name,
        'Остаток': float(b.liters_left or 0),
        'Контроль': 'Да' if b.balance_control else 'Нет',
        'Обновлено': format_kz(b.updated_at, '%d.%m.%Y %H:%M') if b.updated_at else '',
    } for b in balances])
    detail_df = pd.DataFrame(detail_rows)

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        sheet_name = 'all_clients_report'
        summary_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=1)
        client_start = len(summary_df) + 5
        client_summary_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=client_start)
        addendum_start = client_start + len(client_summary_df) + 4
        addendum_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=addendum_start)
        balance_start = addendum_start + len(addendum_df) + 4
        balance_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=balance_start)
        detail_start = balance_start + len(balance_df) + 4
        detail_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=detail_start)

        ws = writer.sheets[sheet_name]
        ws['A1'] = f'Сводка по всем клиентам ({category_label})'
        ws[f'A{client_start + 1}'] = 'Сводка по клиентам'
        ws[f'A{addendum_start + 1}'] = 'Сводка по доп. соглашениям'
        ws[f'A{balance_start + 1}'] = 'Остатки по договорам'
        ws[f'A{detail_start + 1}'] = 'Детальный отчёт'

        widths = {
            'A': 16, 'B': 20, 'C': 18, 'D': 16, 'E': 24, 'F': 14, 'G': 14, 'H': 14,
            'I': 12, 'J': 12, 'K': 14, 'L': 18, 'M': 18, 'N': 14, 'O': 16, 'P': 20,
            'Q': 12, 'R': 14,
        }
        for col, width in widths.items():
            ws.column_dimensions[col].width = width
        ws.freeze_panes = f'A{detail_start + 2}'

    output.seek(0)
    suffix = month or (f'{date_from}_{date_to}' if date_from or date_to else 'all')
    suffix = suffix.replace(':', '-').replace('/', '-')
    if category:
        suffix = f'{category}_{suffix}'
    return send_file(output, as_attachment=True, download_name=f'allclients_report_{suffix}.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@reports_bp.get('/reports/all')
@login_required
def reports_all_page():
    start, end, date_from, date_to, month = _resolve_period()
    category = _selected_category()
    category_label = _category_label(category)
    talons = _filter_talons(Talon.query.order_by(Talon.created_at.desc()), start, end)
    talons = _filter_talons_by_category(talons, category)
    _rows, summary_rows, balance_rows = _build_all_reports_data(talons, category)
    total_balance_liters = sum(float(item['Остаток'] or 0) for item in balance_rows)

    total_count = len(talons)
    active_count = used_count = expired_count = blocked_count = 0
    total_liters = active_liters = used_liters = expired_liters = blocked_liters = 0.0
    for t in talons:
        liters = float(t.liters or 0)
        total_liters += liters
        state = t.effective_state
        if state == 'used':
            used_count += 1
            used_liters += liters
        elif state == 'expired':
            expired_count += 1
            expired_liters += liters
        elif state == 'blocked':
            blocked_count += 1
            blocked_liters += liters
        else:
            active_count += 1
            active_liters += liters

    page = request.args.get('page', 1, type=int)
    per_page = 200
    start_idx = max((page - 1) * per_page, 0)
    end_idx = start_idx + per_page
    rows = _rows[start_idx:end_idx]
    total_pages = max(1, (len(_rows) + per_page - 1) // per_page)
    pagination = {
        'page': page,
        'per_page': per_page,
        'total': len(_rows),
        'pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'prev_num': page - 1,
        'next_num': page + 1,
    }

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
        selected_category=category,
        selected_category_label=category_label,
        pagination=pagination,
    )




@reports_bp.get('/reports/shifts')
@login_required
def shift_reports_page():
    agzs_id = request.args.get('agzs_id', type=int)
    date_from_raw = (request.args.get('date_from') or '').strip()
    date_to_raw = (request.args.get('date_to') or '').strip()

    date_from, date_from_value = _parse_date_arg(date_from_raw)
    date_to, date_to_value = _parse_date_arg(date_to_raw)

    query = Shift.query.filter(Shift.is_closed.is_(True)).join(AGZS).order_by(Shift.closed_at.desc(), Shift.id.desc())
    if agzs_id:
        query = query.filter(Shift.agzs_id == agzs_id)

    shifts = query.all()
    filtered = []
    for s in shifts:
        closed_dt = s.closed_at or s.opened_at
        closed_date = closed_dt.date() if closed_dt and hasattr(closed_dt, 'date') else closed_dt
        if date_from and closed_date and closed_date < date_from:
            continue
        if date_to and closed_date and closed_date > date_to:
            continue
        filtered.append(s)

    total_shifts = len(filtered)
    total_talons = sum(int(s.total_talons or 0) for s in filtered)
    total_liters = sum(float(s.total_liters or 0) for s in filtered)
    total_amount = sum(float(s.total_amount or 0) for s in filtered)

    agzs_summary_map = {}
    for s in filtered:
        agzs_name = s.agzs.name if s.agzs else '—'
        bucket = agzs_summary_map.setdefault(agzs_name, {
            'agzs_name': agzs_name,
            'shifts_count': 0,
            'total_talons': 0,
            'total_liters': 0.0,
            'total_amount': 0.0,
            'last_closed_at': None,
        })
        bucket['shifts_count'] += 1
        bucket['total_talons'] += int(s.total_talons or 0)
        bucket['total_liters'] += float(s.total_liters or 0)
        bucket['total_amount'] += float(s.total_amount or 0)
        if s.closed_at and (bucket['last_closed_at'] is None or s.closed_at > bucket['last_closed_at']):
            bucket['last_closed_at'] = s.closed_at

    agzs_summary = sorted(agzs_summary_map.values(), key=lambda x: x['agzs_name'])
    agzs_list = AGZS.query.order_by(AGZS.name.asc()).all()

    return render_template(
        'shift_reports.html',
        shifts=filtered,
        agzs_list=agzs_list,
        agzs_summary=agzs_summary,
        total_shifts=total_shifts,
        total_talons=total_talons,
        total_liters=total_liters,
        total_amount=total_amount,
        selected_agzs_id=agzs_id,
        date_from=date_from_value,
        date_to=date_to_value,
        format_kz=format_kz,
    )


@reports_bp.get('/reports')
@login_required
def reports_index():
    clients = Client.query.order_by(Client.name.asc()).all()
    counterparty_clients = []
    employee_clients = []
    for c in clients:
        resolved = _resolved_client_category(c)
        if resolved == 'counterparty':
            counterparty_clients.append(c)
        else:
            employee_clients.append(c)
    return render_template('reports_index.html', clients=clients, counterparty_clients=counterparty_clients, employee_clients=employee_clients)

