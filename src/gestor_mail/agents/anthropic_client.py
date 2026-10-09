from collections.abc import Callable
from typing import Self

from anthropic import Anthropic

from ..config import LlmConfig


class AnthropicClient:
    """LLM client backed by the Anthropic API.

        Can be used as a context manager, which creates the SDK client on entry
        and closes it on exit.

        Args:
            config: Model and request settings.
            api_key_provider: Function that returns the API key. It is called
                only when the SDK client is created.
        """
    def __init__(self, llmconfig: LlmConfig, api_provider: Callable[[], str]):
        self._config = llmconfig
        self._client: Anthropic | None = None
        self._api_provider = api_provider

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

#region "Public methods"
    def connect(self):
        """Create the SDK client. No request is sent to the API yet.

        Does nothing if the client already exists.

        Raises:
            RuntimeError: If the API key provider cannot get the key.
        """
        if self._client is not None:
            return
        self._client = Anthropic(api_key=self._api_provider())

    def disconnect(self):
        if self._client is None:
            return
        self._client.close()
        self._client = None

    def create(self):
        ...
#endregion
