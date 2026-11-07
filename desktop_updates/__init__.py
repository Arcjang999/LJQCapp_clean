"""Signed, program-only desktop updates; never imports the business database."""

from .protocol import BASE_RELEASE, UPDATER_VERSION, UpdateError

__all__ = ["BASE_RELEASE", "UPDATER_VERSION", "UpdateError"]
