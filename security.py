import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

from flask import abort, current_app, g, jsonify, request, session
from flask_login import current_user

from models import db, AuditLog, RateLimitEvent


CSRF_EXEMPT_ENDPOINTS = {
    'tg_api_scan',
    'auth.login_post',
}


def get_client_ip() -> str:
    forwarded = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    if forwarded:
        return forwarded
    return request.remote_addr or 'unknown'


def get_csrf_token() -> str:
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


def validate_csrf() -> None:
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return
    if request.path == '/login' or request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return

    # Для загрузки файлов не читаем request.form первым делом.
    # request.form заставляет Werkzeug полностью разобрать multipart/form-data,
    # из-за чего большие файлы могут привести к Gunicorn timeout.
    sent_token = request.headers.get('X-CSRF-Token')
    if not sent_token and request.is_json:
        payload = request.get_json(silent=True) or {}
        sent_token = payload.get('csrf_token')
    if not sent_token and request.content_length and request.content_length < 1024 * 1024:
        sent_token = request.form.get('csrf_token')
    if not sent_token:
        sent_token = request.args.get('csrf_token')

    real_token = session.get('_csrf_token')
    if not real_token or not sent_token or not secrets.compare_digest(str(real_token), str(sent_token)):
        abort(400, description='Недействительный CSRF токен.')


def record_rate_limit_event(category: str, key: str):
    event = RateLimitEvent(category=category, key=key)
    db.session.add(event)
    db.session.commit()


def is_rate_limited(category: str, key: str, limit: int, window_seconds: int) -> bool:
    threshold = datetime.utcnow() - timedelta(seconds=window_seconds)
    count = (
        RateLimitEvent.query
        .filter(
            RateLimitEvent.category == category,
            RateLimitEvent.key == key,
            RateLimitEvent.created_at >= threshold,
        )
        .count()
    )
    return count >= limit


def check_rate_limit_or_429(category: str, key: str, limit: int, window_seconds: int, message: str):
    if is_rate_limited(category, key, limit, window_seconds):
        if request.path.startswith('/tg/api/'):
            return jsonify({'ok': False, 'error': 'rate_limited', 'message': message}), 429
        abort(429, description=message)
    record_rate_limit_event(category, key)
    return None


def log_audit(action: str, message: str, object_type: str | None = None, object_id: int | None = None):
    try:
        row = AuditLog(
            username=current_user.username if getattr(current_user, 'is_authenticated', False) else None,
            user_role=getattr(current_user, 'role', None) if getattr(current_user, 'is_authenticated', False) else None,
            action=action,
            object_type=object_type,
            object_id=object_id,
            ip_address=get_client_ip(),
            message=message,
        )
        db.session.add(row)
        db.session.flush()
        current_app.logger.info("AUDIT %s user=%s obj=%s#%s msg=%s", action, row.username or "system", object_type or "-", object_id or "-", message)
    except Exception:
        db.session.rollback()


def verify_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 600):
    if not init_data or not bot_token:
        return False, 'missing_init_data', None

    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.pop('hash', None)
    if not received_hash:
        return False, 'missing_hash', None

    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
    secret_key = hmac.new(b'WebAppData', bot_token.encode('utf-8'), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode('utf-8'), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return False, 'bad_hash', None

    auth_date_raw = data.get('auth_date')
    try:
        auth_date = int(auth_date_raw)
    except Exception:
        return False, 'bad_auth_date', None

    if int(datetime.utcnow().timestamp()) - auth_date > max_age_seconds:
        return False, 'stale_init_data', None

    user_raw = data.get('user')
    user_obj = None
    if user_raw:
        try:
            user_obj = json.loads(user_raw)
        except Exception:
            user_obj = None

    return True, 'ok', user_obj
