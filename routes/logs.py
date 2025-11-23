from flask import jsonify, request

from core.helpers import login_required, render_view
from database.db import Log, Users


def register_logs(app):
    @app.route('/logs')
    @login_required
    def logs_page():
        return render_view('logs.html')

    @app.route('/api/logs')
    @login_required
    def api_logs():
        user_q = request.args.get('user', '').strip().lower()
        action_q = request.args.get('action', '').strip().lower()
        table_q = request.args.get('table', '').strip().lower()
        start = request.args.get('start')
        end = request.args.get('end')

        q = Log.query.join(Log.user)

        if user_q:
            q = q.filter(Users.username.ilike(f"%{user_q}%"))
        if action_q:
            q = q.filter(Log.action.ilike(f"%{action_q}%"))
        if table_q:
            q = q.filter(Log.target_table.ilike(f"%{table_q}%"))
        if start:
            q = q.filter(Log.created_at >= f"{start} 00:00:00")
        if end:
            q = q.filter(Log.created_at <= f"{end} 23:59:59")

        entries = q.order_by(Log.created_at.desc()).all()

        out = []
        for e in entries:
            out.append({
                'id': e.id,
                'user': e.user.username,
                'action': e.action,
                'table': e.target_table,
                'target_id': e.target_id,
                'details': e.details,
                'created_at': e.created_at.strftime('%d/%m/%Y %H:%M:%S')
            })
        return jsonify(out)
