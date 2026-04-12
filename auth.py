from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user

from models import User, db
from security import get_client_ip, check_rate_limit_or_429, log_audit

auth_bp = Blueprint("auth", __name__)

ALLOWED_SITE_USERS = {"Erdaulet1997", "Gulbara2002", "Erlan2003", "Dana"}


@auth_bp.get("/login")
def login_get():
    if current_user.is_authenticated:
        return redirect(url_for("clients.list_clients"))
    return render_template("login.html")


@auth_bp.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    ip = get_client_ip()

    limited = check_rate_limit_or_429(
        category='login',
        key=f'{ip}:{username.lower()}',
        limit=7,
        window_seconds=300,
        message='Слишком много попыток входа. Подождите 5 минут.',
    )
    if limited:
        return limited

    user = User.query.filter_by(username=username).first()

    if not user or not user.check_password(password):
        log_audit('login_failed', f'Неудачная попытка входа для {username or "unknown"}', 'user', user.id if user else None)
        db.session.commit()
        flash("Неверный логин или пароль", "danger")
        return redirect(url_for("auth.login_get"))

    if user.username not in ALLOWED_SITE_USERS:
        flash("Доступ запрещен", "danger")
        return redirect(url_for("auth.login_get"))

    login_user(user)
    session.permanent = True
    log_audit('login_success', f'Успешный вход пользователя {user.username}', 'user', user.id)
    db.session.commit()
    return redirect(url_for("clients.list_clients"))


@auth_bp.post("/logout")
@login_required
def logout():
    log_audit('logout', f'Выход пользователя {current_user.username}', 'user', current_user.id)
    db.session.commit()
    logout_user()
    return redirect(url_for("auth.login_get"))
