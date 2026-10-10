"""Errors raised by model clients."""


class ModelUnavailable(RuntimeError):
    """The configured model backend could not be used (not running, bad key, bad output)."""
