from functools import wraps
from datetime import date, datetime
from zoneinfo import ZoneInfo
from flask import flash, redirect, request
from flask_login import current_user

KZ_TZ = ZoneInfo("Asia/Almaty")

ROLE_LABELS = {
    "director": "Директор",
    "deputy_director": "Заместитель директора",
    "executor": "Исполнитель",
    "admin": "Администратор",
}

STATUS_LABELS = {
    "active": "Активен",
    "used": "Использован",
    "expired": "Срок действия истек",
    "blocked": "Заблокирован",
}


def has_role(*roles):
    return current_user.is_authenticated and current_user.role in roles


def require_roles(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not has_role(*roles):
                flash('Недостаточно прав.', 'danger')
                return redirect(request.referrer or '/')
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def kz_now():
    return datetime.now(KZ_TZ).replace(tzinfo=None)


def to_kz(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(KZ_TZ)
    return dt.astimezone(KZ_TZ)


def format_kz_datetime(dt, fmt='%d.%m.%Y %H:%M'):
    kz_dt = to_kz(dt)
    return kz_dt.strftime(fmt) if kz_dt else ''


def talon_status_code(talon):
    if getattr(talon, 'state', None) == 'used' or getattr(talon, 'used_at', None):
        return 'used'
    if getattr(talon, 'state', None) == 'blocked':
        return 'blocked'
    if getattr(talon, 'valid_to', None) and talon.valid_to < date.today():
        return 'expired'
    return 'active'


def talon_status(talon):
    return STATUS_LABELS.get(talon_status_code(talon), 'Активен')


def role_label(role):
    return ROLE_LABELS.get(role, 'Пользователь')
