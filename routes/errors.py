from core.helpers import render_view


def register_errors(app):
    @app.errorhandler(404)
    def not_found(error):
        return render_view('404.html'), 404

    @app.errorhandler(500)
    def server_error(error):
        return render_view('500.html'), 500
