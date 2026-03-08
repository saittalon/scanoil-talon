from functools import wraps
from datetime import date
from flask import flash, redirect, request
from flask_login import current_user


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


def talon_status(talon):
    if getattr(talon, 'state', None) == 'used':
        return 'used'
    if getattr(talon, 'state', None) == 'blocked':
        return 'blocked'
    if getattr(talon, 'valid_to', None) and talon.valid_to < date.today():
        return 'expired'
    return getattr(talon, 'state', None) or 'active'
