"""Decisions taken by the mail agent about a single mail."""

from dataclasses import dataclass
from enum import Enum


class Action(Enum):
    """The actions the agent is allowed to choose"""
    NONE = "none"
    MARK_AS_READ = "mark_as_read"
    MOVE = "move"
    TRASH = "trash"
    CREATE_DRAFT = "create_draft"


@dataclass(frozen=True)
class DraftContent:
    """Content of a draft the agent wants to create"""
    to: tuple[str, ...]
    subject: str
    body: str
    cc: tuple[str, ...]

    def __post_init__(self):
        if not self.to:
            raise ValueError("A draft needs at least one recipient")

@dataclass
class Decision:
    """What the agent decides to do with a mail and why
    
    Only the parameters that make sense dor the chosen action can be set."""

    action: Action
    reason: str
    target_folder: str | None = None
    draft: DraftContent | None = None

    def __post_init__(self):
        is_move = self.action is Action.MOVE
        is_draft = self.action is Action.CREATE_DRAFT

        if is_move and not self.target_folder:
            raise ValueError("A move decision needs a target folder")
        if not is_move and self.target_folder is not None:
            raise ValueError("Only a move decision can have a target folder")

        if is_draft and not self.draft:
            raise ValueError("A create draft decision needs a valid body.")
        if not is_draft and self.draft is not None:
            raise ValueError("Only a create draft decision can have a draft content")