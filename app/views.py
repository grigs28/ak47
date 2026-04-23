from flask import Blueprint, render_template, session
from app.auth import admin_required

bp = Blueprint('views', __name__)

@bp.route('/login')
def login():
    return render_template('login.html')

@bp.route('/')
@admin_required
def dashboard():
    return render_template('dashboard.html',
                         username=session.get('display_name', session.get('username', 'Admin')))

@bp.route('/files')
@admin_required
def files():
    return render_template('files.html')

@bp.route('/files/<int:file_id>')
@admin_required
def file_detail(file_id):
    return render_template('file_detail.html', file_id=file_id)

@bp.route('/browse')
@admin_required
def browse():
    return render_template('browse.html')

@bp.route('/settings')
@admin_required
def settings():
    return render_template('settings.html')

@bp.route('/logs')
@admin_required
def logs():
    return render_template('logs.html')

@bp.route('/scan')
@admin_required
def scan():
    return render_template('scan.html',
                         username=session.get('display_name', session.get('username', 'Admin')))
