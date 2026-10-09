"""Module for custom errors"""

class LlmError(Exception):
    """A request to the language model failed (network, auth, limits...)."""


class LlmInvalidResponseError(LlmError):
    """The model answered, but not in the expected structure."""