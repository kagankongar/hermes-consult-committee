from flask import Flask

def create_app():
    app = Flask(__name__)
    from .invoices import bp as invoices_bp
    from .webhooks import bp as webhooks_bp
    app.register_blueprint(invoices_bp)
    app.register_blueprint(webhooks_bp)
    return app
