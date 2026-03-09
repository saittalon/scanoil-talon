from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from models import User

auth_bp = Blueprint("auth", __name__)

ALLOWED_SITE_USERS = {"Erdaulet1997", "Gulbara2002", "Erlan2003"}


@auth_bp.get("/login")
def login_get():
    return "LOGIN PAGE TEST


@auth_bp.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()

    user = User.query.filter_by(username=username).first()

    if not user or not user.check_password(password):
        flash("Неверный логин или пароль", "danger")
        return redirect(url_for("auth.login_get"))

    if user.username not in ALLOWED_SITE_USERS:
        flash("Доступ запрещен", "danger")
        return redirect(url_for("auth.login_get"))

    login_user(user)
    return redirect(url_for("clients.list_clients"))


@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login_get"))
