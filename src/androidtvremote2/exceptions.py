"""Exceptions."""


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class ConnectionClosed(Exception):
    """Error to indicate a regular EOF was received or the connection was aborted or closed."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
