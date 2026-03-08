from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user
from sqlalchemy import func
from models import User

auth_bp = Blueprint('auth', __name__)


def _username_aliases(username: str):
    normalized = (username or '').strip().lower()
    aliases = {normalized}
    if normalized in {'zamdirector', 'замдиректора', 'zam', 'deputy', 'deputydirector', 'deputy_director'}:
        aliases.update({'zamdirector', 'deputydirector', 'deputy_director'})
    if normalized in {'director', 'директор'}:
        aliases.add('director')
    if normalized in {'executor', 'исполнитель'}:
        aliases.add('executor')
    return tuple(aliases)


@auth_bp.get('/login')
def login_get():
    return render_template('login.html')


@auth_bp.post('/login')
def login_post():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    aliases = _username_aliases(username)
    user = User.query.filter(func.lower(User.username).in_(aliases)).first()
    if not user or not user.check_password(password):
        flash('Неверный логин или пароль', 'danger')
        return redirect(url_for('auth.login_get'))

    login_user(user)
    return redirect(url_for('clients.list_clients'))


@auth_bp.get('/logout')
def logout():
    logout_user()
    return redirect(url_for('auth.login_get'))
