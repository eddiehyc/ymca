class YmcaError(Exception):
    """Base exception for YMCA."""


class ConfigError(YmcaError):
    """Raised when the user config is missing or invalid."""


class SecretError(YmcaError):
    """Raised when the YNAB API key cannot be loaded."""


class ApiError(YmcaError):
    """Raised when YNAB API interaction fails."""


class StateError(YmcaError):
    """Raised when local state cannot be parsed or saved."""


class UserInputError(YmcaError):
    """Raised when CLI input is invalid or incomplete."""
