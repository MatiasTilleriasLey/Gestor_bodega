from flask import jsonify, session
from sqlalchemy.exc import SQLAlchemyError

from core.helpers import clean_text, get_json_body, login_required, render_view
from database.db import Log, Users, db


def register_profile(app):
    @app.route('/perfil', methods=['GET'])
    @login_required
    def perfil():
        user = Users.query.get_or_404(session['user_id'])
        return render_view('perfil.html',
                           username=user.username,
                           name=user.name,
                           email=user.email)

    @app.route('/api/perfil', methods=['POST'])
    @login_required
    def api_actualizar_perfil():
        user = Users.query.get_or_404(session['user_id'])
        old_name = user.name
        old_email = user.email
        data = get_json_body()
        name = clean_text(data.get('name'))
        email = clean_text(data.get('email'))
        if not name or not email:
            return jsonify(error="Nombre y email son obligatorios"), 400

        try:
            user.name = name
            user.email = email
            db.session.add(Log(
                user_id=session['user_id'],
                action='update_profile',
                target_table='users',
                target_id=user.id,
                details=f"Actualizó su perfil: nombre '{old_name}' -> '{user.name}', correo '{old_email}' -> '{user.email}'"
            ))
            db.session.commit()
            return jsonify(message="Perfil actualizado"), 200
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify(error="Error al actualizar perfil"), 500

    @app.route('/api/perfil/password', methods=['POST'])
    @login_required
    def api_cambiar_password():
        user = Users.query.get_or_404(session['user_id'])
        data = get_json_body()
        old = data.get('old_pass', '')
        new = data.get('new_pass', '')
        rep = data.get('repet_new_pass', '')

        if not old or not new or not rep:
            return jsonify(error="Todos los campos de contraseña son obligatorios"), 400
        if new != rep:
            return jsonify(error="Las contraseñas nuevas no coinciden"), 400
        if not user.check_password(old):
            return jsonify(error="Contraseña actual incorrecta"), 400

        try:
            user.password = new
            db.session.add(Log(
                user_id=session['user_id'],
                action='change_password',
                target_table='users',
                target_id=user.id,
                details="Cambió su contraseña desde la pantalla de perfil"
            ))
            db.session.commit()
            return jsonify(message="Contraseña cambiada"), 200
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify(error="Error al cambiar contraseña"), 500

    @app.route('/perfil/theme', methods=['POST'])
    @login_required
    def perfil_theme():
        data = get_json_body()
        theme = data.get('theme')
        if theme not in ('dark', 'light'):
            return jsonify(error="Tema inválido"), 400

        user = Users.query.get(session['user_id'])
        user.theme = theme
        db.session.add(Log(
            user_id=session['user_id'],
            action='change_theme',
            target_table='users',
            target_id=user.id,
            details=f"Cambió el tema de la interfaz a '{theme}'"
        ))
        db.session.commit()
        session['theme'] = theme
        return jsonify(message="Tema actualizado"), 200
