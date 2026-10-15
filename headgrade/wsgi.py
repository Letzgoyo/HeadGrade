"""Production entry point:  gunicorn -b 0.0.0.0:8080 headgrade.wsgi:app"""
from .site import create_app

app = create_app()
