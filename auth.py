from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user
from models import User

ALLOWED_SITE_USERS = {"director", "zamdirector", "executor"}
ALLOWED_SITE_ROLES = {"director", "deputy_director", "zamdirector", "executor"}

auth_bp = Blueprint('auth', __name__)


def _normalize_username(value: str) -> str:
    raw = (value or '').strip()
    compact = raw.replace('_', '').replace('-', '').replace(' ', '').lower()
    aliases = {
        'director': 'director',
        'директор': 'director',
        'zamdirector': 'zamdirector',
        'deputydirector': 'zamdirector',
        'zamdir': 'zamdirector',
        'замдиректора': 'zamdirector',
        'замдиректор': 'zamdirector',
        'executor': 'executor',
        'исполнитель': 'executor',
    }
    return aliases.get(compact, raw)


@auth_bp.get('/login')
def login_get():
    return render_template('login.html')


@auth_bp.post('/login')
def login_post():
    username = _normalize_username(request.form.get('username', ''))
    password = request.form.get('password', '').strip()

    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        flash('Неверный логин или пароль', 'danger')
        return redirect(url_for('auth.login_get'))

    if user.username not in ALLOWED_SITE_USERS and user.role not in ALLOWED_SITE_ROLES:
        flash('Доступ разрешен только директору, замдиректору и исполнителю', 'danger')
        return redirect(url_for('auth.login_get'))

    login_user(user)
    return redirect(url_for('clients.list_clients'))


@auth_bp.get('/logout')
def logout():
    logout_user()
    return redirect(url_for('auth.login_get'))
