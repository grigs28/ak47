from flask import Blueprint

bp = Blueprint('views', __name__)

@bp.route('/')
def index():
    return 'OK'
