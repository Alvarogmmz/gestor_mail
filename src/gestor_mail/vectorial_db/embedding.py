"""Module that contains the embedding functionalities."""

import logging
from typing import Self

from ollama import Client, EmbedResponse, RequestError, ResponseError

logger = logging.getLogger(__name__)

class EmbeddingHelper:
    """Turns texts into embedding vectors with an Ollama model.

    Args:
        model: Name of the embedding model, e.g. "bge-m3".
        host: URL of the Ollama server.
    """

    def __init__(self, model: str, host: str = "http://localhost:11434"):
        self._model = model
        self._host = host
        self._client: Client | None = None

    def __enter__(self)-> Self:
        self._connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._disconnect()

    @property
    def model(self) -> str:
        """Name of the model that makes the vectors."""
        return self._model

    @property
    def is_connected(self) -> bool:
        """Whether there is an open connection to the server."""
        return self._client is not None

    def _connect(self):
        if self.is_connected:
            return

        try:
            self._client = Client(self._host)
        except Exception:
            logger.exception(
                "Could not reach the Ollama server."
            )
            raise

    def _disconnect(self):
        """Log out and close the connection.
        
        Does nothing if not connected. The client is always left
        disconnected, even if logging out fails.
        """
        connection = self._client
        if connection is None:
            return

        try:
            connection.close()
        except Exception:
            logger.warning(
                "Error while logging out; the connection is closed anyway.",
                exc_info=True,
            )
        finally:
            self._client = None

    def generate_embedding(self, text: str) -> list[float]:
        """Return the embedding vector of a text.

        Raises:
            RuntimeError: If not connected.
            ValueError: If the text is empty.
            RequestError, ResponseError, ConnectionError: If Ollama fails,
                e.g. the model is not installed or the server is not running.
        """
        connection = self._client
        if connection is None:
            raise RuntimeError("Not connected to Ollama. Use EmbeddingHelper as a context manager.")

        if not text.strip():
            raise ValueError("The text is empty, nothing to embed.")

        try:
            response: EmbedResponse = connection.embed(model=self._model, input=text)
        except RequestError:
            logger.exception("An error ocurred while generating the embedding")
            raise
        except ResponseError:
            logger.exception("Ollama could not embed the text with model %s", self._model)
            raise
        except ConnectionError:
            logger.exception("Ollama server not found or is not running.")
            raise

        # One input text gives exactly one vector.
        return list(response.embeddings[0])
