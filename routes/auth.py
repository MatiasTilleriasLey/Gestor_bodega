import time
from flask import jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.exc import SQLAlchemyError

from core.helpers import clean_text, get_json_body, render_view
from database.db import Log, Users, db


def register_auth(app):
    @app.before_request
    def ensure_first_user():
        if Users.query.count() == 0:
            if request.endpoint not in ('setup', 'static', 'login'):
                return redirect(url_for('setup'))

    @app.route('/setup', methods=['GET', 'POST'])
    def setup():
        if Users.query.count() > 0:
            return redirect(url_for('login'))

        if request.method == 'GET':
            return render_view('setup.html')

        data = get_json_body()
        name = clean_text(data.get('name'))
        username = clean_text(data.get('username'))
        email = clean_text(data.get('email'))
        password = data.get('password') or ''
        password2 = data.get('password2') or ''

        if not all([name, username, email, password, password2]):
            return jsonify(error="Todos los campos son obligatorios"), 400

        if password != password2:
            return jsonify(error="Las contraseñas no coinciden"), 400

        if Users.query.filter_by(username=username).first():
            return jsonify(error="Usuario ya existe"), 400

        try:
            user = Users(
                name=name,
                username=username,
                email=email,
                is_Admin=True
            )
            user.password = password
            db.session.add(user)
            db.session.commit()
            return jsonify(message="Usuario administrador creado"), 201

        except SQLAlchemyError:
            db.session.rollback()
            return jsonify(error="Error creando usuario"), 500

    @app.route('/')
    def index():
        if 'user_id' in session:
            return redirect(url_for('dashboard'))
        return render_view('index.html')

    @app.route('/api/login', methods=["POST"])
    def login():
        data = get_json_body()
        if not data:
            return jsonify({"error": "JSON inválido"}), 400

        username = data.get('username')
        password = data.get('password')
        if not username or not password:
            return jsonify({"error": "Faltan credenciales"}), 422

        user = Users.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            return jsonify({"error": "Usuario o contraseña incorrectos"}), 401
        session.clear()
        session['user_id'] = user.id
        session['is_Admin'] = user.is_Admin
        session['name'] = user.name
        session['theme'] = user.theme
        db.session.add(Log(
            user_id=user.id,
            action='login',
            target_table='users',
            target_id=user.id,
            details=f"Inicio de sesión de {user.username}"
        ))
        db.session.commit()
        return jsonify({
            "user_id": user.id,
            "username": user.username
        })

    @app.route('/api/logout')
    def logout():
        user_id = session.get('user_id')
        username = session.get('name')
        try:
            if user_id:
                db.session.add(Log(
                    user_id=user_id,
                    action='logout',
                    target_table='users',
                    target_id=user_id,
                    details=f"Cierre de sesión de {username or 'usuario'}"
                ))
                db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
        session.clear()
        return redirect(url_for('index'))
