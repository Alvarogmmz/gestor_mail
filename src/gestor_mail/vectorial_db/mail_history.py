"""History of decisions taken on mails, stored as vectors for later search."""

import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from ..agents.decision import Action, Decision, PastDecision
from ..models.mail import Mail
from ..models.prompt import html_to_text
from .database import VDB, DecidedBy, MailInfo
from .embedding import EmbeddingHelper

logger = logging.getLogger(__name__)

MAX_EMBEDDING_CHARS = 1000
MAX_SNIPPET_CHARS = 300
# Similar mails older than this are not used as examples: habits change.
SIMILAR_MAX_AGE = timedelta(days=365)

# Line that introduces a quoted reply, e.g. "On Mon, 1 Jan, Ana wrote:" or
# "El lun, 1 ene, Ana escribió:". Everything after it is the previous thread.
_REPLY_INTRO = re.compile(r"^(on|el)\b.*\b(wrote|escribió):\s*$", re.IGNORECASE)
# Usual separators before a forwarded or original message.
_ORIGINAL_MESSAGE = re.compile(
    r"^-{2,}\s*(original message|mensaje original|forwarded message|mensaje reenviado)",
    re.IGNORECASE,
)


class MailHistory:
    """Saves and finds past decisions on mails.

    Joins the embedding model and the vector database, and speaks in terms of
    mails and decisions, so the rest of the application does not need to know
    about vectors or Qdrant.

    Args:
        embedder: Connected client that turns texts into vectors.
        db: Connected vector database.
    """

    def __init__(self, embedder: EmbeddingHelper, db: VDB, score_threshold: float = 0.6):
        self._embedder = embedder
        self._db = db
        self._score_threshold = score_threshold

    def find_examples(self, mail: Mail, limit: int = 5) -> list[PastDecision]:
        """Find the user's past decisions on mails like this one.

        Mails from the same sender come first, because they are the best
        hint of what the user will do. The rest are mails with similar
        content from any sender.

        Args:
            mail: The mail being decided.
            limit: Maximum number of decisions to return.

        Returns:
            The past decisions, without repeated mails. Empty if there is
            nothing relevant.

        Raises:
            Any error from the embedder or the database.
        """
        header = mail.header
        address = header.sender.lower()
        domain = address.rpartition("@")[2]

        found: list[tuple[dict[str, Any], float | None]] = [
            (record.payload or {}, None)
            for record in self._db.find_by_sender(
                address, domain, exclude_message_id=header.message_id, limit=limit
            )
        ]

        text = build_embedding_text(mail)
        if text:
            vector = self._embedder.generate_embedding(text)
            similar = self._db.find_similar(
                vector,
                exclude_message_id=header.message_id,
                since=datetime.now(UTC) - SIMILAR_MAX_AGE,
                score_threshold=self._score_threshold,
                limit=limit,
            )
            found += [(point.payload or {}, point.score) for point in similar]

        examples: list[PastDecision] = []
        seen: set[str] = set()
        for payload, score in found:
            message_id = payload.get("message_id")
            if message_id in seen:
                continue
            seen.add(message_id)

            example = _to_past_decision(payload, score)
            if example is not None:
                examples.append(example)
            if len(examples) == limit:
                break
        return examples

    def record(self, mail: Mail, decision: Decision, decided_by: DecidedBy) -> None:
        """Save the decision taken on a mail.

        Saving the same mail again replaces the previous decision.
        Mails without Message-ID or without text are skipped with a warning.

        Raises:
            Any error from the embedder or the database.
        """
        header = mail.header
        if not header.message_id:
            logger.warning("Mail UID %s has no Message-ID; not recorded.", mail.uid)
            return

        text = build_embedding_text(mail)
        if not text:
            logger.warning("Mail UID %s has no text; not recorded.", mail.uid)
            return

        vector = self._embedder.generate_embedding(text)
        info = MailInfo(
            message_id=header.message_id,
            from_address=header.sender,
            subject=header.subject,
            snippet=text[:MAX_SNIPPET_CHARS],
            date=header.date,
            is_list=header.is_list,
            has_attachments=bool(mail.attachments),
            action=decision.action,
            target_folder=decision.target_folder,
            decided_by=decided_by,
        )
        self._db.upsert([(info, vector)], embedding_model=self._embedder.model)


def _to_past_decision(payload: dict[str, Any], score: float | None) -> PastDecision | None:
    """Convert a stored payload into a PastDecision, or None if it is invalid."""
    try:
        return PastDecision(
            from_address=payload.get("from_address", ""),
            subject=payload.get("subject", ""),
            snippet=payload.get("snippet", ""),
            action=Action(payload["action"]),
            target_folder=payload.get("target_folder"),
            score=score,
        )
    except (KeyError, ValueError):
        logger.warning("Skipping stored mail with an invalid decision: %r", payload.get("message_id"))
        return None


def build_embedding_text(mail: Mail, max_chars: int = MAX_EMBEDDING_CHARS) -> str:
    """Build the text that represents a mail in the vector database.

    It is the subject followed by the start of the body, as plain text and
    without the quoted thread. The same function must be used to save mails
    and to search for them, so that the vectors can be compared.

    Returns:
        The text, or an empty string if the mail has no subject nor body.
    """
    subject = mail.header.subject.strip()
    body = _clean_body(_body_as_text(mail))
    text = f"{subject}\n\n{body}" if subject and body else subject or body
    return text[:max_chars].strip()


def _body_as_text(mail: Mail) -> str:
    """Return the body as plain text, using the HTML part if there is no text."""
    body = mail.body
    if body is None:
        return ""
    if body.text:
        return body.text
    if body.html:
        return html_to_text(body.html)
    return ""


def _clean_body(text: str) -> str:
    """Remove the quoted thread, the signature and repeated blank lines."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _REPLY_INTRO.match(stripped) or _ORIGINAL_MESSAGE.match(stripped):
            break
        # Standard signature separator ("-- ").
        if line.rstrip() == "--":
            break
        if stripped.startswith(">"):
            continue
        lines.append(stripped)

    cleaned = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()
