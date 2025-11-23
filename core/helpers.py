import bleach
import secrets
from functools import wraps
from flask import render_template, request, session, redirect, url_for, current_app

# Sanitization defaults shared across the app
ALLOWED_TAGS = []
ALLOWED_ATTRS = {}


def clean_text(value: str) -> str:
    """Sanitize incoming text fields with shared bleach rules."""
    return bleach.clean(
        (value or '').strip(),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        strip=True
    )


def get_json_body(default=None):
    """Safe JSON loader with predictable default."""
    return request.get_json(silent=True) or (default if default is not None else {})


def render_view(template_name: str, **context):
    """Render templates injecting session identity helpers."""
    base_context = {
        'name': session.get('name'),
        'is_Admin': session.get('is_Admin', False)
    }
    base_context.update(context)
    return render_template(template_name, **base_context)


def register_context_processors(app):
    @app.context_processor
    def inject_globals():
        token = session.get('csrf_token')
        if not token:
            token = secrets.token_hex(16)
            session['csrf_token'] = token
        return {
            'name': session.get('name'),
            'is_Admin': session.get('is_Admin', False),
            'csrf_token': token
        }


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('index'))
        if not session.get('is_Admin', False):
            return redirect(url_for('logout'))
        return f(*args, **kwargs)
    return decorated_function


def is_allowed_file(filename: str) -> bool:
    allowed_ext = current_app.config.get('ALLOWED_IMAGE_EXT', set())
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_ext


def parse_dmy(s: str):
    """Convierte 'dd/mm/aaaa' a datetime a las 00:00; devuelve None si no aplica."""
    from datetime import datetime
    try:
        d = datetime.strptime(s.strip(), "%d/%m/%Y")
        return d
    except Exception:
        return None


def to_iso(dt):
    if hasattr(dt, "isoformat"):
        try:
            return dt.isoformat(sep=' ', timespec='seconds')
        except Exception:
            return dt.isoformat()
    return dt
