"""Uvicorn entry point."""

from app.factory import create_app

app = create_app()
