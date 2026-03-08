from functools import wraps
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import flash, redirect, request
from flask_login import current_user

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


def has_role(*roles):
    return current_user.is_authenticated and current_user.role in roles


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
