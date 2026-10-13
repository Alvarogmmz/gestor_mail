from collections.abc import Callable, Iterable
from typing import Any, Self

from anthropic import Anthropic, APIError
from anthropic.types import (
    MessageParam,
    TextBlockParam,
    ToolChoiceToolParam,
    ToolParam,
)

from ..config import LlmConfig
from ..errors import LlmError, LlmInvalidResponseError
from ..models.prompt import Prompt

_TOOL_NAME = "record_decision"


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

    def generate_structured(self, prompt: Prompt, schema: dict[str, Any]) -> dict[str, Any]:
        """Ask the model for an answer that follows the given JSON schema.

        Raises:
            LlmError: If the request to the API fails.
            LlmInvalidResponseError: If the model does not answer with the tool.
        """
        answer = self.generate(
            message=prompt.user,
            tools={
                "name": _TOOL_NAME,
                "description": "Record the decision taken for the email.",
                "input_schema": schema,
            },
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            system=prompt.system,
        )
        if not isinstance(answer, dict):
            raise LlmInvalidResponseError("The model did not answer with the tool.")
        return answer

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