from flask import abort, jsonify, request, session
from sqlalchemy.exc import SQLAlchemyError

from core.helpers import admin_required, clean_text, get_json_body, render_view
from database.db import Log, Users, db


def register_users(app):
    @app.route('/usuarios')
    @admin_required
    def usuarios():
        users = Users.query.order_by(Users.username).all()
        return render_view("usuarios.html", users=users)

    @app.route('/usuarios/editar/<id>', methods=["GET", "POST"])
    @admin_required
    def usuarios_edit(id):
        user = Users.query.get(id)
        if not user:
            abort(404)
        if request.method == "GET":
            return render_view("editar.html", user_id=id, user=user)
        if request.method == "POST":
            data = get_json_body()
            if not data:
                return jsonify({"error": "JSON inválido"}), 400
            try:
                user.name = clean_text(data.get("name"))
                user.email = clean_text(data.get("email"))
                user.is_Admin = bool(data["is_Admin"])
                db.session.add(Log(
                    user_id=session['user_id'],
                    action='edit_user',
                    target_table='users',
                    target_id=user.id,
                    details=f"Editó usuario {user.username}: nombre '{user.name}', correo '{user.email}', admin={user.is_Admin}"
                ))
                db.session.commit()
                return jsonify({"message": "Usuario actualizado"}), 200
            except SQLAlchemyError:
                db.session.rollback()
                return jsonify({"message": "Ocurrio un error"}), 400

    @app.route('/usuarios/editar/password/<id>', methods=["POST"])
    @admin_required
    def usuarios_edit_password(id):
        user = Users.query.get_or_404(id)

        default_pwd = "changeme123"
        try:
            user.password = default_pwd
            db.session.add(Log(
                user_id=session['user_id'],
                action='reset_password',
                target_table='users',
                target_id=user.id,
                details=f"Restableció contraseña de {user.username} al valor temporal por defecto"
            ))
            db.session.commit()
            return jsonify({
                "message": f"Contraseña de usuario {user.username} restablecida a '{default_pwd}'"
            }), 200

        except SQLAlchemyError:
            db.session.rollback()
            return jsonify({"error": "Error al restablecer la contraseña"}), 500

    @app.route('/usuarios/eliminar/<id>', methods=["POST"])
    @admin_required
    def usuarios_delete(id):
        user = Users.query.get(id)
        if not user:
            abort(404)
        try:
            db.session.delete(user)
            db.session.add(Log(
                user_id=session['user_id'],
                action='delete_user',
                target_table='users',
                target_id=user.id,
                details=f"Eliminó usuario {user.username} (id {user.id})"
            ))
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify({"error": "No se pudo eliminar el usuario"}), 500

        return jsonify({"message": "Usuario eliminado correctamente"}), 200

    @app.route('/usuarios/crear', methods=["POST"])
    @admin_required
    def usuarios_crear():
        try:
            data = get_json_body()
            required = ("username", "password", "email", "name")
            if not all(data.get(field) for field in required):
                return jsonify({"error": "Faltan campos obligatorios"}), 400
            new_user = Users(
                username=clean_text(data.get("username")),
                password=data.get("password"),
                email=clean_text(data.get("email")),
                name=clean_text(data.get("name")),
                is_Admin=bool(data["is_Admin"])
            )
            db.session.add(new_user)
            db.session.add(Log(
                user_id=session['user_id'],
                action='create_user',
                target_table='users',
                target_id=new_user.id,
                details=f"Creó usuario {new_user.username} (admin={new_user.is_Admin}, correo {new_user.email})"
            ))
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify({"error": "No se puede crear el usuario"}), 500

        return jsonify({"message": "Usuario Creado Correctamente"}), 200
