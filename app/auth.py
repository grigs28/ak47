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
    callback = request.url_root.rstrip('/') + '/callback'
    return redirect(f"{yz_url}/login?from={callback}")

@bp.route('/callback')
def callback():
    ticket = request.args.get('ticket')
    if not ticket:
        return "缺少 ticket", 400

    yz_url = current_app.config['YZ_LOGIN_URL']
    try:
        resp = requests.get(f"{yz_url}/api/ticket/verify", params={'ticket': ticket}, timeout=10)
        data = resp.json()
    except Exception as e:
        return f"验证失败: {e}", 500

    if not data.get('ok'):
        return data.get('msg', '认证失败'), 401

    # 管理员名单从环境变量读取，逗号分隔
    admin_users = current_app.config.get('ADMIN_USERS', [])
    username = data.get('username', '')
    is_admin = username in admin_users

    session['user_id'] = data['id']
    session['username'] = username
    session['display_name'] = data.get('display_name', username)
    session['is_admin'] = is_admin

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
