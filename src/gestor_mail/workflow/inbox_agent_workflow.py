import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from ..agents.decision import Action, Decision
from ..config import WorkFlowConfig
from ..models.mail import Mail
from ..services.mail_service import MailHelper
from ..storage.state_repository import FolderState, StateRepository

logger = logging.getLogger(__name__)


class MailDecider(Protocol):

    def __init__(self):
        ...

    def decide(self, mail: Mail) -> Decision:
        ...

@dataclass
class RunSummary:
    processed: int = 0
    actions: Counter[Action] = field(default_factory=Counter)

    def record(self, decision: Decision) -> None:
        """Count a processed mail and the action taken on it."""
        self.processed += 1
        self.actions[decision.action] += 1

class InboxAgentWorkflow:

    def __init__(
            self,
            service: MailHelper,
            decider: MailDecider,
            state_repository: StateRepository,
            config: WorkFlowConfig
    ):
        self.service = service
        self.decider = decider
        self.state_repository = state_repository
        self.config = config

    def run(self) -> RunSummary:
        """Process the next batch of new mails.

        On the first run, or if the folder's UIDVALIDITY has changed, no mail
        is processed: the current newest mail is stored as the starting point.

        Returns:
            A summary of the processed mails and the actions taken.

        Raises:
            Any error from the service, the decider or the state repository.
            The state of the mails processed before the error is kept.
        """
        folder = self.config.folder
        summary = RunSummary()

        uid_validity = self.service.get_uid_validity(folder)
        state = self.state_repository.load(folder)

        if state is None:
            logger.info("No previous state for %s; starting from now", folder)
            self._start_from_now(folder, uid_validity)
            return summary

        if state.uid_validity != uid_validity:
            logger.warning(
                "UIDVALIDITY of %s changed (%s -> %s); starting from now.",
                folder, state.uid_validity, uid_validity,
            )
            self._start_from_now(folder, uid_validity)
            return summary

        mails = self.service.fetch_new(folder, after_uid=state.last_uid, limit=self.config.batch_size)
        logger.info("Found %d new mails in %s", len(mails), folder)

        for mail in mails:
            decision = self._validate(folder, self.decider.decide(mail))
            logger.info("UID %s | %s | %s - > %s (%s)",
                        mail.uid, mail.header.sender, mail.header.subject,
                        decision.action.value, decision.reason,)
            if self.config.dry_run:
                logger.info("Dry run: action not executed")
            else:
                self._execute(folder, mail, decision)

            self.state_repository.save(folder, FolderState(uid_validity, mail.uid))
            summary.record(decision)
        return summary


    def _start_from_now(self, folder: str, uid_validity: int) -> None:
        highest_uid = self.service.get_highest_uid(folder) or 0
        self.state_repository.save(folder, FolderState(uid_validity, highest_uid))

    def _validate(self, folder: str, decision: Decision) -> Decision:
        """Check a decision against the workflow settings.

        Returns:
            The same decision if it is allowed, or a NONE decision explaining
            why it was rejected.
        """
        if decision.action is Action.MOVE:
            target = decision.target_folder
            if target == folder:
                return self._reject(decision, f"target folder {target!r} is the current one")
            if target not in self.config.allowed_folders:
                return self._reject(decision, f"target folder {target!r} is not allowed")
        return decision

    @staticmethod
    def _reject(decision: Decision, problem: str) -> Decision:
        """Replace a rejected decision with NONE, keeping the original reason."""
        logger.warning("Rejected %s decision: %s.", decision.action.value, problem)
        return Decision(
            Action.NONE,
            reason=f"Rejected {decision.action.value}: {problem}. Original reason: {decision.reason}",
        )

    def _execute(self, folder: str, mail: Mail, decision: Decision) -> None:
        """Apply a validated decision to a mail."""
        uids = [mail.uid]

        match decision.action:
            case Action.NONE:
                pass
            case Action.MARK_AS_READ:
                self.service.mark_as_read(folder, uids)
            case Action.MOVE:
                assert decision.target_folder is not None  # Guaranteed by Decision.
                self.service.move(folder, uids, decision.target_folder)
            case Action.TRASH:
                self.service.move_to_trash(folder, uids)
            case Action.CREATE_DRAFT:
                assert decision.draft is not None  # Guaranteed by Decision.
                draft = decision.draft
                self.service.create_draft(
                    to=list(draft.to),
                    subject=draft.subject,
                    body=draft.body,
                    cc=list(draft.cc),
                )