"""Serverless entrypoint: re-export the Flask app defined in bot.py."""
from bot import app

__all__ = ["app"]
