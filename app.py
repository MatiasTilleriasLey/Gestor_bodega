import os
import secrets
from flask import Flask, abort, request, session

from database.db import db, init_db
from core.helpers import register_context_processors
from routes.auth import register_auth
from routes.dashboard import register_dashboard
from routes.dispatches import register_dispatches
from routes.errors import register_errors
from routes.inventory import register_inventory
from routes.logs import register_logs
from routes.orders import register_orders
from routes.profile import register_profile
from routes.users import register_users


def create_app():
    app = Flask(__name__)

    app.config.from_mapping(
        SECRET_KEY='your-secret-key',
        DEBUG=True,
    )
    app.config['ALLOWED_IMAGE_EXT'] = {'png', 'jpg', 'jpeg', 'webp'}
    app.config['UPLOAD_DIR'] = os.path.join(app.root_path, 'static', 'uploads')
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    os.makedirs(app.config['UPLOAD_DIR'], exist_ok=True)

    init_db(app)
    register_context_processors(app)

    register_auth(app)
    register_profile(app)
    register_logs(app)
    register_dashboard(app)
    register_inventory(app)
    register_dispatches(app)
    register_orders(app)
    register_users(app)
    register_errors(app)

    @app.before_request
    def csrf_protect():
        # Genera token si no existe (también manejado en context_processor)
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(16)
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
            if not token or token != session.get('csrf_token'):
                abort(403)

    return app


app = create_app()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
