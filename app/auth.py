import requests
from functools import wraps
from flask import Blueprint, session, redirect, request, current_app, url_for

bp = Blueprint('auth', __name__)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login_page'))
        if not session.get('is_admin'):
            return "权限不足", 403
        return f(*args, **kwargs)
    return decorated

@bp.route('/login')
def login_page():
    yz_url = current_app.config['YZ_LOGIN_URL']
    redirect_uri = request.url_root.rstrip('/') + '/callback'
    return redirect(f"{yz_url}/auth/sso?app_id=ak47&redirect_uri={redirect_uri}")

@bp.route('/callback')
def callback():
    ticket = request.args.get('ticket')
    if not ticket:
        return "缺少 ticket", 400

    yz_url = current_app.config['YZ_LOGIN_URL']
    try:
        resp = requests.get(f"{yz_url}/auth/verify-ticket", params={'ticket': ticket}, timeout=10)
        data = resp.json()
    except Exception as e:
        return f"验证失败: {e}", 500

    if not data.get('user_id'):
        return "认证失败", 401

    if not data.get('is_admin'):
        return "仅管理员可访问", 403

    session['user_id'] = data['user_id']
    session['username'] = data.get('username', '')
    session['display_name'] = data.get('display_name', '')
    session['is_admin'] = data.get('is_admin', 0)

    return redirect(url_for('views.dashboard'))

@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login_page'))

@bp.route('/auth/me')
@login_required
def me():
    return {
        'user_id': session.get('user_id'),
        'username': session.get('username'),
        'display_name': session.get('display_name'),
        'is_admin': session.get('is_admin'),
    }
