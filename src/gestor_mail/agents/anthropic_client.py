from collections.abc import Callable, Iterable
from typing import Self

from anthropic import Anthropic, APIError
from anthropic.types import (
    MessageParam,
    TextBlockParam,
    ToolChoiceToolParam,
    ToolParam,
)

from ..config import LlmConfig
from ..errors import LlmError, LlmInvalidResponseError


class AnthropicClient:
    def __init__(self, llmconfig: LlmConfig, api_key_provider: Callable[[], str]):
        
        self._config = llmconfig
        self._api_key_provider = api_key_provider

        self._client: Anthropic | None = None

    def __enter__(self) -> Self | None:
        self._get_client()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._disconnect()

    def generate(self, message: str, tools: dict, tool_choice: dict, system: str):
        client = self._require_client()

        messages_param: Iterable[MessageParam] = [MessageParam(role="user", content=message)]
        tools_param: Iterable[ToolParam] = [ToolParam(**tools)]
        tool_choice_param: ToolChoiceToolParam = ToolChoiceToolParam(**tool_choice)
        system_param: Iterable[TextBlockParam] = [TextBlockParam(type="text", text=system)]

        try:
            response = client.messages.create(model=self._config.model, max_tokens=self._config.max_token,
                            messages=messages_param, tools=tools_param, tool_choice=tool_choice_param,
                            system=system_param)
        except APIError as e:
            raise LlmError(f"Anthropic API request failed: {e}") from e
        
        if response.stop_reason == "max_tokens":
            raise LlmInvalidResponseError(
                "Response was truncated (max_tokens reached)."
            )

        for block in response.content:
            if block.type == "tool_use":
                return block.input

    def _get_client(self):
        """Create the SDK client. No request is sent to the API yet.

        Does nothing if the client already exists.

        Raises:
            RuntimeError: If the API key provider cannot get the key.
        """
        if self._client is not None:
            return
        self._client = Anthropic(api_key=self._api_key_provider())

    def _disconnect(self):
        if self._client is None:
            return
        self._client.close()
        self._client = None

    def _require_client(self):

        if self._client is None:
            raise RuntimeError("Anthropic client is not connected. Call connect() first.")
        return self._client