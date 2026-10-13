"""Application configuration, loaded from enviroment variables.

Values are read from the process environment and from a .env file, if any.
This is the only module that reads environment variables"""


import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass(frozen=True)
class ImapConfig:
    """Configuration settings for the IMAP server."""
    host: str
    port: int
    user: str
    use_ssl: bool
    timeout: float


@dataclass(frozen=True)
class WorkFlowConfig:
    """Settings for the inbox agent workflow."""
    folder: str
    allowed_folders: tuple[str, ...]
    state_path: Path
    batch_size:int
    dry_run: bool

@dataclass(frozen=True)
class LlmConfig:
    url: str
    model:str
    max_token: int


@dataclass(frozen=True)
class AgentConfig:
    """Settings for the mail agent."""
    system_prompt: str
    max_body_chars: int


@dataclass(frozen=True)
class AppConfig:
    """All the application settings."""

    imap: ImapConfig
    workflow: WorkFlowConfig
    llm: LlmConfig
    agent: AgentConfig
    log_level: str


def load_config() -> AppConfig:
    """Load and validate the configuration.

    Returns:
        The complete configuration.

    Raises:
        RuntimeError: If a required variable is missing or a value is invalid.
    """
    load_dotenv()

    use_ssl = _get_bool("IMAP_USE_SSL", default=True)
    imap = ImapConfig(
        host=_require("IMAP_HOST"),
        port=_get_int("IMAP_PORT", default=993 if use_ssl else 143),
        user=_require("IMAP_USERNAME"),
        use_ssl=use_ssl,
        timeout=_get_float("IMAP_TIMEOUT", default=30.0),
    )

    workflow = WorkFlowConfig(
        folder=os.environ.get("WATCHED_FOLDER", "INBOX"),
        allowed_folders=_get_list("ALLOWED_FOLDERS"),
        state_path=Path(os.environ.get("STATE_PATH", "data/state.json")),
        batch_size=_get_int("BATCH_SIZE", default=20),
        dry_run=_get_bool("DRY_RUN", default=True),
    )

    llm = LlmConfig(
        url=os.environ.get("LLM_URL", ""),
        model=os.environ.get("LLM_MODEL", ""),
        max_token=_get_int("LLM_MAX_TOKEN", 8192)
    )

    agent = AgentConfig(
        system_prompt=_read_required_file("RULES_PATH", default=Path("rules.md")),
        max_body_chars=_get_int("MAX_BODY_CHARS", default=8000),
    )

    return AppConfig(
        imap=imap,
        workflow=workflow,
        llm=llm,
        agent=agent,
        log_level=_get_log_level("LOG_LEVEL", default="INFO"),
    )

def get_imap_password() -> str:
    """Return the IMAP password. Called only when connecting.

    Raises:
        RuntimeError: If the password is not configured.
    """
    return _require("IMAP_PASSWORD")

def get_lmm_key() -> str:
    """Return the Llm API key necesary.
    Raises:
        RuntimeError: If the api key is not configured."""
    return _require("LLM_API_KEY")

def _get_bool(name: str, default: bool) -> bool:
    """Return a variable as a bool, or the default if it is not set."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    raise RuntimeError(f"{name} must be true or false, got {raw!r}.")


def _get_int(name: str, default: int) -> int:
    """Return a variable as a positive int, or the default if it is not set."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}.") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number, got {value}.")
    return value


def _get_float(name: str, default: float) -> float:
    """Return a variable as a positive float, or the default if it is not set."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number, got {raw!r}.") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number, got {value}.")
    return value


def _get_list(name: str) -> tuple[str, ...]:
    """Return a comma-separated variable as a tuple, ignoring empty items."""
    raw = os.environ.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _read_required_file(name: str, default: Path) -> str:
    """Return the content of the file a variable points to, or of the default.

    The file must exist and must not be empty.
    """
    raw = os.environ.get(name, "").strip()
    path = Path(raw) if raw else default
    if not path.is_file():
        raise RuntimeError(f"{name}: file not found: {path.resolve()}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise RuntimeError(f"{name}: file is empty: {path.resolve()}")
    return content


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required configuration variable: {name}")
    return value

def _get_log_level(name: str, default: str) -> str:
    """Return a variable as a logging level name, or the default if not set."""
    raw = os.environ.get(name, "").strip().upper()
    if not raw:
        return default
    if raw not in LOG_LEVELS:
        raise RuntimeError(f"{name} must be one of {sorted(LOG_LEVELS)}, got {raw!r}.")
    return raw

