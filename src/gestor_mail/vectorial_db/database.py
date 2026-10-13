"""Module that contains the vectorial database functionalities"""

import logging
from typing import Self

from qdrant_client import QdrantClient, models

logger = logging.getLogger(__name__)

class VDB:
    def __init__(self):
        self._client: QdrantClient | None = None
        self._collection: str


    def __enter__(self)-> Self:
        self._connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._disconnect()

    def _is_connected(self):
        return self._client is not None

    def _connect(self):
        if self._is_connected:
            return

        try:
            self._client = QdrantClient(url='http://localhost:6333', )
        except Exception:
            logger.exception(
                "Could not reach the Qdrant server."
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

    def upsert(self):
        pass

    def retrieve(self,):
        filters = models.Filter(
            must=[
                models.FieldCondition(key="mailbox_id", match=models.MatchValue(value="")), 
                models.FieldCondition(key="sender_domain", match=models.MatchValue(value="")),
                models.FieldCondition(key="sender_domain", match=models.MatchValue(value="")),
                ]
            )