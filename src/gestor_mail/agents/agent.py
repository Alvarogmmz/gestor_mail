import logging
from typing import Any, Protocol

from ..errors import LlmInvalidResponseError
from ..models.mail import Mail
from ..models.prompt import Prompt
from .decision import Action, Decision, DraftContent, PastDecision

logger = logging.getLogger(__name__)

class LlmClient(Protocol):
    """Anything able to get a structured answer from a language model."""

    def generate_structured(
        self, prompt: "Prompt", schema: dict[str, Any]
    ) -> dict[str, Any]:
        ...

class DecisionHistory(Protocol):
    """Anything able to find the user's past decisions on similar mails."""

    def find_examples(self, mail: Mail, limit: int) -> list[PastDecision]:
        ...

class AgentHelper:
    """Decides what to do with each mail by asking a language model.

    Invalid answers from the model become NONE decisions. Only infrastructure
    failures (LlmError) are raised, which stops the workflow.

    Args:
        llm: Client used to talk to the language model.
        system_prompt: Instructions for the model, the same for every mail.
        allowed_folders: Folders the model can move mails to.
        max_body_chars: Maximum length of the body sent to the model.
        history: Past decisions of the user, shown to the model as examples.
            None to decide without examples.
        max_examples: Maximum number of past decisions sent with each mail.
    """

    def __init__(
        self,
        llm: LlmClient,
        system_prompt: str,
        allowed_folders: tuple[str, ...],
        max_body_chars: int,
        history: DecisionHistory | None = None,
        max_examples: int = 5,
    ):
        self._llm = llm
        self._system = system_prompt
        self._allowed_folders = allowed_folders
        self._max_body_chars = max_body_chars
        self._history = history
        self._max_examples = max_examples

        # Moving is only offered if there is somewhere to move to.
        self._actions = [
            action
            for action in Action
            if action is not Action.MOVE or allowed_folders
        ]
        self._schema = self._build_schema()

    def decide(self, mail: Mail) -> Decision:
        """Decide what to do with a mail.

        Args:
            mail: The mail to analyse.

        Returns:
            The decision. If the model answers something invalid, a NONE
            decision explaining the problem.

        Raises:
            LlmError: If the request to the model fails.
        """
        examples = self._find_examples(mail)
        prompt = Prompt.from_mail(mail, self._system, self._max_body_chars, examples)

        try:
            answer = self._llm.generate_structured(prompt, self._schema)
        except LlmInvalidResponseError as error:
            logger.warning("Invalid answer for mail UID %s: %s", mail.uid, error)
            return Decision(Action.NONE, reason=f"Invalid answer from the model: {error}")

        return self._parse(mail, answer)

    def _find_examples(self, mail: Mail) -> list[PastDecision]:
        """Return the past decisions to show with a mail.

        The history only helps the model, so if it fails the mail is decided
        without examples. Examples the model could not repeat (moves to
        folders that are no longer allowed) are left out.
        """
        if self._history is None:
            return []

        try:
            examples = self._history.find_examples(mail, self._max_examples)
        except Exception:
            logger.warning(
                "Could not get past decisions for mail UID %s; deciding without them.",
                mail.uid, exc_info=True,
            )
            return []

        usable = [
            example for example in examples
            if example.action is not Action.MOVE
            or example.target_folder in self._allowed_folders
        ]
        logger.debug("Mail UID %s: %d past decisions as examples.", mail.uid, len(usable))
        return usable

    def _build_schema(self) -> dict[str, Any]:
        """Build the JSON schema the model's answer must follow."""
        properties: dict[str, Any] = {
            "action": {
                "type": "string",
                "enum": [action.value for action in self._actions],
                "description": "What to do with the email.",
            },
            "reason": {
                "type": "string",
                "description": "Short explanation of the decision.",
            },
            "draft_body": {
                "type": "string",
                "description": "Body of the draft reply. Only for create_draft.",
            },
        }
        if self._allowed_folders:
            properties["target_folder"] = {
                "type": "string",
                "enum": list(self._allowed_folders),
                "description": "Destination folder. Only for move.",
            }

        return {
            "type": "object",
            "properties": properties,
            "required": ["action", "reason"],
        }

    def _parse(self, mail: Mail, answer: dict[str, Any]) -> Decision:
        """Convert the model's answer into a Decision, or NONE if invalid."""
        try:
            action = Action(answer["action"])
            reason = str(answer.get("reason", "")).strip() or "No reason given."

            target_folder = None
            if action is Action.MOVE:
                target_folder = answer.get("target_folder")

            draft = None
            if action is Action.CREATE_DRAFT:
                body = str(answer.get("draft_body", "")).strip()
                if not body:
                    raise ValueError("create_draft without draft_body")
                draft = DraftContent(
                    to=(mail.header.sender,),
                    cc=(),
                    subject=_reply_subject(mail.header.subject),
                    body=body,
                )

            return Decision(action, reason, target_folder=target_folder, draft=draft)

        except (KeyError, TypeError, ValueError) as error:
            logger.warning("Invalid answer for mail UID %s: %s", mail.uid, error)
            return Decision(Action.NONE, reason=f"Invalid answer from the model: {error}")


def _reply_subject(subject: str) -> str:
    """Return the subject for a reply, adding "Re:" only once."""
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"