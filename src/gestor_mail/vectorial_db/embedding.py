"Module that contains the embedding functionalities. """

import logging
from collections.abc import Sequence
from typing import Self

from ollama import Client, EmbedResponse, RequestError

logger = logging.getLogger(__name__)

class EmbeddingHelper:

    def __init__(self, model):
        self._model = model
        self._client: Client | None = None

    def __enter__(self)-> Self:
        self._connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._disconnect()

    @property
    def is_connected(self) -> bool:
        """Whether there is an open connection to the server."""
        return self._client is not None

    def _connect(self):
        if self.is_connected:
            return

        try:
            self._client = Client('http://localhost:11434')
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
            self._connection = None
            self._selected_folder = None
            self._readonly = False

    def generate_embedding(self, text: str) -> Sequence[Sequence[float]]:
        connection = self._client
        if connection is None:
            logger.warning("There is not an istance of the ollama client")
            return []

        try:
            embedding: EmbedResponse = connection.embed(model=self._model, input=text)
            return embedding.embeddings
        except RequestError:
            logger.exception("An error ocurred while generating the embedding")
            raise