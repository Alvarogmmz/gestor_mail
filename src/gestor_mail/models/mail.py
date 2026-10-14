"""Module that contains the Mail structure: header, body, attachments..."""

from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage


@dataclass
class Header:
    subject: str
    sender: str
    date: datetime | None
    message_id: str | None = None
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    is_list: bool = False

    @classmethod
    def from_message(cls, message: EmailMessage) -> "Header":
        """Read the header of a mail. Missing headers become empty values.

        sender, to and cc hold only the addresses (e.g. "a@b.com"), in
        lowercase, without display names.
        """
        senders = _addresses(message, "From")
        message_id = message["Message-ID"]
        date_header = message["Date"]

        return Header(
            subject=str(message["Subject"] or ""),
            sender=senders[0] if senders else "",
            date=getattr(date_header, "datetime", None),
            message_id=str(message_id).strip() if message_id else None,
            to=_addresses(message, "To"),
            cc=_addresses(message, "Cc"),
            # Mailing lists and newsletters carry one of these headers.
            is_list=message["List-Id"] is not None
            or message["List-Unsubscribe"] is not None,
        )


def _addresses(message: EmailMessage, name: str) -> list[str]:
    """Return the addresses of an address header, or an empty list."""
    header = message[name]
    if header is None:
        return []
    addresses = getattr(header, "addresses", ())
    return [address.addr_spec.lower() for address in addresses if address.addr_spec]


@dataclass
class Body:
    text: str | None = None
    html: str | None = None

    @classmethod
    def from_message(cls, message: EmailMessage) -> "Body":
        body = Body()
        text_part = message.get_body(preferencelist=("plain",))
        if text_part is not None:
            body.text = text_part.get_content()

        text_part = message.get_body(preferencelist=("html",))
        if text_part is not None:
            body.html = text_part.get_content()

        return body

@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int
    content: bytes | None = None

    @classmethod
    def from_part(cls, part: EmailMessage) -> "Attachment":
        return Attachment(part.get_filename() or "unnamed", part.get_content_type(), len(part.get_content()), part.get_content())


@dataclass
class Mail:
    uid: int
    folder: str
    header: Header
    flags: set[str] = field(default_factory=set)
    body: Body | None = None
    attachments: list[Attachment] = field(default_factory=list)

    @classmethod
    def from_bytes(
        cls, raw: bytes, uid: int, folder: str, flags: set[str]
    ) -> "Mail":
        """Obtains the Mail structure from a mail message
        Args:
            raw (bytes): raw mail.
            uid (int); relative message id.
            folder (str)"""
        email_message = message_from_bytes(raw, policy=policy.default)
        header = Header.from_message(email_message)
        body = Body.from_message(email_message)

        attachments = []
        for part in email_message.iter_attachments():
            attachments.append(Attachment.from_part(part))

        mail = Mail(uid, folder, header, flags, body, attachments)

        return mail

    @property
    def is_read(self) -> bool:
        return r"\Seen" in self.flags