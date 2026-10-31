"""WSGI entry point for gunicorn: gunicorn wsgi:app"""
from nxblogger import create_app

app = create_app()
