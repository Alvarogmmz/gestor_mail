
import logging
import sys
from pathlib import Path

from .agents.fake_agent import FakeAgent
from .clients.imap_client import ImapClient
from .config import get_imap_password, load_config
from .services.mail_service import MailHelper
from .storage.state_repository import StateRepository
from .workflow.inbox_agent_workflow import InboxAgentWorkflow

logger = logging.getLogger(__name__)


def configure_logging():
    """Set up logging at INFO level.

    The final level is applied once the configuration has been loaded, so
    that errors while loading it are already logged with the right format.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

def main():

    configure_logging()

    try:
        config = load_config()
    except RuntimeError as error:
        logger.error("Invalid configuration: %s", error)
        return 1

    logging.getLogger().setLevel(config.log_level)

    if config.workflow.dry_run:
        logger.warning("DRY RUN: decisions will be logged but not executed.")

    state_repository = StateRepository(Path(config.workflow.state_path))

    client = ImapClient(config.imap, get_imap_password)
    decider = FakeAgent()
    try:
        with client:
            service = MailHelper(client, sender=config.imap.user)
            workflow = InboxAgentWorkflow(
                service, decider, state_repository, config.workflow
            )
            print(client.list_folders())
            summary = workflow.run()
    except Exception:
        logger.exception("The workflow run failed.")
        return 1

    actions = ", ".join(
        f"{action.value}: {count}" for action, count in summary.actions.items()
    )
    logger.info(
        "Run finished. Processed %d mails (%s).", summary.processed, actions or "none"
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())