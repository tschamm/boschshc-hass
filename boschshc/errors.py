"""Errors for the SHC component."""
from homeassistant.exceptions import HomeAssistantError


class ShcException(HomeAssistantError):
    """Base class for Shc exceptions."""


class CannotConnect(ShcException):
    """Unable to connect to the bridge."""


class AuthenticationRequired(ShcException):
    """Unknown error occurred."""
