
import json
from dataclasses import dataclass
from html.parser import HTMLParser

from .mail import Mail

#region "Prompt"
@dataclass(frozen=True)
class Prompt:
    """Text sent to the language model: system instructions and user message."""
    
    system: str
    user: str

    @classmethod
    def from_mail(cls, mail: Mail, system: str, max_body_chars: int) -> "Prompt":
        """Build the prompt to decide what to do with a mail.

        Args:
            mail: The mail to analyse.
            system: The system prompt, the same for every mail.
            max_body_chars: Maximum length of the body sent to the model.

        Returns:
            The prompt, ready to be sent to any LLM client.
        """
        return cls(system=system, user=_build_user_message(mail, max_body_chars))


def _build_user_message(mail: Mail, max_body_chars: int) -> str:
    """Describe a mail as a JSON document for the model."""
    header = mail.header
    data = {
        "from": header.sender,
        "to": header.to,
        "cc": header.cc,
        "subject": header.subject,
        "date": header.date.isoformat() if header.date else None,
        "is_read": mail.is_read,
        "attachments": [attachment.filename for attachment in mail.attachments],
        "body": _get_body_text(mail, max_body_chars),
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def _get_body_text(mail: Mail, max_body_chars: int) -> str:
    """Return the body as plain text, truncated to the given limit."""
    body = mail.body
    if body is None:
        return ""

    if body.text:
        text = body.text
    elif body.html:
        text = _html_to_text(body.html)
    else:
        return ""

    text = text.strip()
    if len(text) > max_body_chars:
        return text[:max_body_chars] + "\n[... truncated]"
    return text

class _HtmlTextExtractor(HTMLParser):
    """Collects the visible text of an HTML document."""

    _SKIPPED_TAGS = {"script", "style", "head"}  # noqa: RUF012
    _LINE_BREAK_TAGS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}  # noqa: RUF012

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in self._LINE_BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text, dropping scripts, styles and empty lines."""
    parser = _HtmlTextExtractor()
    parser.feed(html)
    parser.close()
    lines = (line.strip() for line in parser.get_text().splitlines())
    return "\n".join(line for line in lines if line)

#endregion