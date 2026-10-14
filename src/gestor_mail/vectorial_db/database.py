"""Module that contains the vectorial database functionalities"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Self
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from ..agents.decision import Action

logger = logging.getLogger(__name__)

class DecidedBy(Enum):
    """Who took the decision stored with a mail."""
    HUMAN = "human"
    AGENT = "agent"
    AGENT_CONFIRMED = "agent_confirmed"


@dataclass
class MailInfo:
    message_id: str
    from_address: str
    subject: str
    snippet: str
    date: datetime | None
    is_list: bool
    has_attachments: bool
    action: Action
    target_folder: str | None
    decided_by: DecidedBy

    @property
    def point_id(self) -> str:
        """Qdrant ID of the mail. The same Message-ID always gives the same ID,
        so saving a mail twice updates it instead of duplicating it."""
        return str(uuid5(NAMESPACE_URL, self.message_id.strip()))

    def to_payload(self, embedding_model: str) -> dict:
        address = self.from_address.lower()
        return {
            "message_id": self.message_id,
            "from_address": address,
            "from_domain": address.rpartition("@")[2],
            "subject": self.subject,
            "snippet": self.snippet,
            "date": self.date.isoformat() if self.date else None,
            "is_list": self.is_list,
            "has_attachments": self.has_attachments,
            "action": self.action.value,
            "target_folder": self.target_folder,
            "decided_by": self.decided_by.value,
            "embedding_model": embedding_model,
        }


class VDB:
    """Mail history stored in a Qdrant collection.

    Args:
        collection: Name of the Qdrant collection.
        vector_name: Name of the dense vector in the collection. It must match
            the name used when the collection was created.
        batch_size: Maximum number of points sent in each upsert request.
    """

    def __init__(self, collection: str, vector_name: str = "dense", batch_size: int = 128):
        self._client: QdrantClient | None = None
        self._collection = collection
        self._vector_name = vector_name
        self._batch_size = batch_size


    def __enter__(self)-> Self:
        self._connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._disconnect()

    def _is_connected(self):
        return self._client is not None

    def _connect(self):
        if self._is_connected():
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
            self._client = None

    def upsert(
        self,
        records: Sequence[tuple[MailInfo, Sequence[float]]],
        embedding_model: str,
    ) -> None:
        """Save mails in the collection, updating the ones already stored.

        Args:
            records: Each mail with its embedding vector. The vector must be a
                single flat list of floats, made with embedding_model.
            embedding_model: Name of the model that made the vectors. It is
                stored with each mail so the vectors can be rebuilt if the
                model changes.

        Raises:
            RuntimeError: If not connected.
            ValueError: If a vector is empty.
            Any error from Qdrant. Batches sent before the error are kept.
        """
        client = self._require_client()

        points = []
        for info, vector in records:
            if not vector:
                raise ValueError(f"Empty vector for mail {info.message_id!r}.")
            points.append(
                models.PointStruct(
                    id=info.point_id,
                    vector={self._vector_name: list(vector)},
                    payload=info.to_payload(embedding_model),
                )
            )

        for start in range(0, len(points), self._batch_size):
            batch = points[start:start + self._batch_size]
            try:
                client.upsert(
                    collection_name=self._collection,
                    points=batch,
                    wait=True,
                )
            except Exception:
                logger.exception(
                    "Could not save %d mails in %s.", len(batch), self._collection
                )
                raise

        logger.debug("Saved %d mails in %s.", len(points), self._collection)

    def _require_client(self) -> QdrantClient:
        """Return the active client, or raise if not connected."""
        client = self._client
        if client is None:
            raise RuntimeError("Not connected to Qdrant. Use VDB as a context manager.")
        return client

    def find_by_sender(
        self,
        from_address: str,
        from_domain: str,
        exclude_message_id: str | None = None,
        limit: int = 5,
    ) -> list[models.Record]:
        """Find the user's latest decisions on mails from the same sender.

        Looks for the exact address first and, if there is nothing, for the
        same domain.

        Args:
            from_address: Sender address, e.g. "a@b.com".
            from_domain: Sender domain, e.g. "b.com".
            exclude_message_id: Message-ID of the current mail, so it is not
                returned as an example of itself.
            limit: Maximum number of results.

        Returns:
            The stored points, newest first. Empty if nothing matches.

        Raises:
            RuntimeError: If not connected.
            Any error from Qdrant.
        """
        client = self._require_client()

        for key, value in (("from_address", from_address), ("from_domain", from_domain)):
            if not value:
                continue
            records, _ = client.scroll(
                collection_name=self._collection,
                scroll_filter=models.Filter(
                    must=[
                        _human_decision(),
                        models.FieldCondition(key=key, match=models.MatchValue(value=value)),
                    ],
                    must_not=_exclude_message(exclude_message_id),
                ),
                order_by=models.OrderBy(key="date", direction=models.Direction.DESC),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            if records:
                return records
        return []

    def find_similar(
        self,
        vector: Sequence[float],
        exclude_message_id: str | None = None,
        since: datetime | None = None,
        score_threshold: float = 0.6,
        limit: int = 5,
    ) -> list[models.ScoredPoint]:
        """Find the user's decisions on mails similar to a vector.

        Args:
            vector: Embedding of the current mail, made with the same model
                and text recipe as the stored ones.
            exclude_message_id: Message-ID of the current mail, so it is not
                returned as an example of itself.
            since: Only mails received from this date on. None for any date.
            score_threshold: Minimum similarity (0-1). Less similar mails are
                left out, even if that leaves no results.
            limit: Maximum number of results.

        Returns:
            The stored points, most similar first. Empty if nothing matches.

        Raises:
            RuntimeError: If not connected.
            Any error from Qdrant.
        """
        client = self._require_client()

        must = [_human_decision()]
        if since is not None:
            must.append(models.FieldCondition(key="date", range=models.DatetimeRange(gte=since)))

        return client.query_points(
            collection_name=self._collection,
            query=list(vector),
            using=self._vector_name,
            query_filter=models.Filter(
                must=must,
                must_not=_exclude_message(exclude_message_id),
            ),
            score_threshold=score_threshold,
            limit=limit,
            with_payload=True,
        ).points


def _human_decision() -> models.FieldCondition:
    """Condition that keeps only decisions taken by the user."""
    return models.FieldCondition(
        key="decided_by", match=models.MatchValue(value=DecidedBy.HUMAN.value)
    )


def _exclude_message(message_id: str | None) -> list[models.Condition]:
    """Conditions that leave out one mail, or none if there is no Message-ID."""
    if not message_id:
        return []
    return [models.FieldCondition(key="message_id", match=models.MatchValue(value=message_id))]
