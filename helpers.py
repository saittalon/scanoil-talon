from functools import wraps
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import flash, redirect, request
from flask_login import current_user
from sqlalchemy import update

from models import db, Talon

KZ_TZ = ZoneInfo("Asia/Almaty")
UTC_TZ = timezone.utc


def kz_now():
    return datetime.now(KZ_TZ)


def kz_today():
    return kz_now().date()


def to_kz(dt):
    if not dt:
        return None

    # Если datetime без timezone, считаем что он сохранен в UTC
    # и переводим в Казахстанское время
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=UTC_TZ)

    return dt.astimezone(KZ_TZ)


def format_kz(dt, fmt="%d.%m.%Y %H:%M"):
    value = to_kz(dt)
    return value.strftime(fmt) if value else ""


def _expand_roles(roles):
    requested = set(roles)
    expanded = set(requested)

    # zamdirector и deputy_director — одно и то же право, просто разные названия роли.
    if "zamdirector" in requested or "deputy_director" in requested:
        expanded.update({"zamdirector", "deputy_director"})

    # executor и operator в системе используются как рабочие роли одного уровня.
    if "executor" in requested or "operator" in requested:
        expanded.update({"executor", "operator"})

        # Руководство и бухгалтер могут выполнять рабочие действия,
        # но рабочие роли НЕ получают права руководства обратно.
        expanded.update({"director", "zamdirector", "deputy_director", "accountant"})

    return expanded


def has_role(*roles):
    return current_user.is_authenticated and current_user.role in _expand_roles(roles)


def require_roles(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not has_role(*roles):
                flash("Недостаточно прав.", "danger")
                return redirect(request.referrer or "/")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def talon_status(talon):
    if getattr(talon, "state", None) == "used":
        return "used"
    if getattr(talon, "state", None) == "blocked":
        return "blocked"
    if getattr(talon, "valid_to", None) and talon.valid_to < kz_today():
        return "expired"
    return getattr(talon, "state", None) or "active"


def talon_status_label(talon):
    mapping = {
        "active": "Активен",
        "used": "Использован",
        "expired": "Срок действия истек",
        "blocked": "Заблокирован",
    }
    return mapping.get(talon_status(talon), "Активен")


def redeem_talon_atomic(*, code=None, talon_id=None, used_at=None, agzs_id=None, telegram_user_id=None, user_id=None):
    """
    Атомарно списывает талон только один раз.
    Возвращает tuple(status, talon), где status:
    - redeemed
    - not_found
    - expired
    - already_used
    - not_active
    """
    if not code and talon_id is None:
        raise ValueError("code or talon_id is required")

    now_dt = used_at or kz_now()
    today = now_dt.date()

    filters = []
    if code:
        filters.append(Talon.code == code)
    if talon_id is not None:
        filters.append(Talon.id == talon_id)

    talon = Talon.query.filter(*filters).first()
    if not talon:
        return 'not_found', None

    current = talon_status(talon)
    if current == 'expired':
        if talon.state != 'expired':
            talon.state = 'expired'
            db.session.commit()
        return 'expired', talon

    if talon.state == 'used' or talon.used_at is not None:
        return 'already_used', talon

    if talon.state != 'active' or (talon.valid_from and talon.valid_from > today):
        return 'not_active', talon

    values = {
        Talon.state: 'used',
        Talon.used_at: now_dt,
    }
    if agzs_id is not None:
        values[Talon.used_agzs_id] = agzs_id
    if telegram_user_id is not None:
        values[Talon.used_telegram_user_id] = str(telegram_user_id)
    if user_id is not None:
        values[Talon.used_by_user_id] = user_id

    stmt = (
        update(Talon)
        .where(*filters)
        .where(Talon.state == 'active')
        .where(Talon.used_at.is_(None))
        .where(Talon.valid_to >= today)
    )
    if talon.valid_from is not None:
        stmt = stmt.where(Talon.valid_from <= today)

    result = db.session.execute(stmt.values(values))
    if result.rowcount != 1:
        db.session.rollback()
        talon = Talon.query.filter(*filters).first()
        if not talon:
            return 'not_found', None
        current = talon_status(talon)
        if current == 'expired':
            return 'expired', talon
        if talon.state == 'used' or talon.used_at is not None:
            return 'already_used', talon
        return 'not_active', talon

    talon = Talon.query.filter(*filters).first()
    return 'redeemed', talon


def format_talon_number(value):
    raw = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if not raw:
        return '—'
    if len(raw) <= 3:
        return raw
    parts = [raw[i:i+3] for i in range(0, len(raw), 3)]
    return ' '.join(parts)


def talon_display_number(talon):
    if talon is None:
        return '—'
    code = getattr(talon, 'code', None)
    if code:
        return format_talon_number(code)
    serial = getattr(talon, 'serial_number', None)
    return str(serial or '—')
