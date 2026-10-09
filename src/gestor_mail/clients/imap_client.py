"""Module that contains the imap client class."""

import logging
import re
import ssl
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from imaplib import IMAP4, IMAP4_SSL, ParseFlags
from typing import Self

from ..config import ImapConfig
from ..models.mail import Mail

logger = logging.getLogger(__name__)

#region "FolderInfo"
@dataclass(frozen=True)
class FolderInfo:
    """A folder as reported by the IMAP server."""

    name: str
    attributes: frozenset[str]
    delimiter: str | None


@dataclass(frozen=True)
class FolderStatus:
    """Information about a folder, reported by the server when selecting it."""
    message_count: int
    uid_validity: int


_LIST_PATTERN = re.compile(
    rb'\((?P<attributes>[^)]*)\) (?P<delimiter>"(?:[^"\\]|\\.)*"|NIL) (?P<name>.*)'
)


def _unquote(value: str) -> str:
    """Remove IMAP quotes and escapes from a string, if present."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value
#endregion


def _close_quietly(connection: IMAP4) -> None:
    """Close a connection, ignoring any error while doing so."""
    with suppress(IMAP4.error, OSError):
        connection.logout()

#region "ImapClient"
class ImapClient:

    def __init__(self, imapconfig: ImapConfig, password_provider: Callable[[], str]):
        self._config = imapconfig
        self._password_provider = password_provider
        self._connection: IMAP4 | None = None
        self._selected_folder = None
        self._readonly = None
        self._uid_pattern = re.compile(rb"UID (\d+)")
        self._append_pattern = re.compile(rb"APPENDUID \d+ (\d+)")

#region "Public Methods"
    @property
    def is_connected(self) -> bool:
        """Whether there is an open connection to the server."""
        return self._connection is not None

    def connect(self):
        if self._connection is not None:
            return

        config = self._config
        try:
            if config.use_ssl:
                connection = IMAP4_SSL(
                    config.host,
                    config.port,
                    ssl_context=ssl.create_default_context(),
                    timeout=config.timeout,
                )
            else:
                connection = IMAP4(config.host, config.port, timeout=config.timeout)
        except (IMAP4.error, OSError):
            logger.exception(
                "Could not reach the IMAP server %s:%s", config.host, config.port
            )
            raise

        try:
            connection.login(config.user, self._password_provider())
        except Exception:
            # Covers login errors and password provider errors alike.
            logger.exception("Could not log in to %s as %s", config.host, config.user)
            _close_quietly(connection)
            raise

        self._connection = connection

    def disconnect(self):
        """Log out and close the connection.

        Does nothing if not connected. The client is always left
        disconnected, even if logging out fails.
        """
        connection = self._connection
        if connection is None:
            return

        try:
            connection.logout()
        except (IMAP4.error, OSError):
            logger.warning(
                "Error while logging out; the connection is closed anyway.",
                exc_info=True,
            )
        finally:
            self._connection = None
            self._selected_folder = None
            self._readonly = False

    def search(self, *criteria: str, charset: str | None = None) -> list[int]:
        """Search the currently selected folder for mails matching the criteria.

        The criteria use IMAP search syntax and are combined with AND.
        If no criteria are given, all mails in the folder are returned.

        Args:
            *criteria: IMAP search keys, e.g. "UNSEEN", 'FROM "a@b.com"'. 
                Defaults to "ALL" if none are given.
            charset: Encoding of the criteria strings, e.g. "UTF-8".

        Returns:
            The UIDs of the matching mails, in the order returned by the
            server. Empty list if nothing matches.

        Raises:
            RuntimeError: If not connected, if no folder is selected, or if
                the server rejects the search.
            imaplib.IMAP4.error: If a protocol error occurs.
            OSError: If a network error occurs during the search.
        """

        connection = self._connection
        if connection is None:
            logger.warning("Not connected to the IMAP server.")
            raise RuntimeError

        if not self._selected_folder:
            logger.warning("You need to specify a folder to search into.")
            raise RuntimeError

        if not criteria:
            criteria = ("ALL",)

        args: list[str] = []
        if charset is not None:
            args += ["CHARSET", charset]
        args += criteria

        try:
            status, data = connection.uid("SEARCH", *args)
        except (IMAP4.error, OSError):
            logger.exception("A problem occurred while trying to search mails")
            raise

        self._check_ok(status, data, "Search")

        return [int(uid) for uid in data[0].split()]
            


    def fetch(self, uids: list[int], headers_only: bool = False) -> list[Mail]:
        """Download the mails with the given UIDs from the selected folder.

        Mails are fetched with PEEK, so they are not marked as read.

        Args:
            uids: UIDs of the mails to download.
            headers_only: If True, only headers are downloaded, which is much
                faster. The resulting mails will have an empty body.

        Returns:
            The downloaded mails. Empty list if no UIDs are given.

        Raises:
            RuntimeError: If not connected, if no folder is selected, or if
                the server rejects the fetch.
            imaplib.IMAP4.error: If a protocol error occurs.
            OSError: If a network error occurs during the fetch.
        """
        if not uids:
            return []

        connection, folder = self._require_selected_folder()

        uid_set = ",".join(str(uid) for uid in uids)
        parts = "(FLAGS BODY.PEEK[HEADER])" if headers_only else "(FLAGS BODY.PEEK[])"

        try:
            status, data = connection.uid("FETCH", uid_set, parts)
        except (IMAP4.error, OSError):
            logger.exception("A problem occurred while fetching mails: %s", uid_set)
            raise

        self._check_ok(status, data, "Fetch")

        mails: list[Mail] = []
        for index, item in enumerate(data):
            if not isinstance(item, tuple):
                continue

            meta, raw = item

            # Some servers send UID/FLAGS after the body, in the next element.
            next_item = data[index + 1] if index + 1 < len(data) else b""
            info = meta + (next_item if isinstance(next_item, bytes) else b"")

            uid_match = self._uid_pattern.search(info)
            if uid_match is None:
                logger.warning("Skipping fetched item without UID: %r", meta)
                continue

            uid = int(uid_match.group(1))
            flags = {flag.decode() for flag in ParseFlags(info)}

            try:
                mail = Mail.from_bytes(raw, uid, folder, flags)
            except Exception:
                logger.exception("Could not parse mail UID %s in %s; skipping it.", uid, folder)
                continue

            mails.append(mail)

        return mails
            
    def move(self, uids: list[int], target_folder: str) -> None:
        """Move mails from the selected folder to another folder.

        Args:
            uids: UIDs of the mails to move, from the currently selected folder.
            target_folder: Name of the destination folder, e.g. "Trash".

        Raises:
            RuntimeError: If not connected, if no folder is selected, if the
                server does not support MOVE, or if the server rejects it.
            imaplib.IMAP4.error: If a protocol error occurs.
            OSError: If a network error occurs.
        """
        if not uids:
            return
        
        connection, _folder = self._require_selected_folder()

        if "MOVE" not in connection.capabilities:
            raise RuntimeError("The server does not support the MOVE command.")
        
        uid_set = ",".join(str(uid) for uid in uids)

        try:
            status, data = connection.uid("MOVE", uid_set, self._quote(target_folder))
        except (IMAP4.error, OSError):
            logger.exception("A problem occurred while fetching mails: %s", uid_set)
            raise

        self._check_ok(status, data, "Move")

    def add_flags(self, uids: list[int], flags: set[str]):
        """Add flags to mails in the selected folder, keeping existing ones.

        Args:
            uids: UIDs of the mails to modify.
            flags: Flags to add, e.g. {"\\Seen"} or {"\\Seen", "\\Flagged"}.

        Raises:
            RuntimeError: If not connected, if no folder is selected, or if
                the server rejects the operation.
            imaplib.IMAP4.error: If a protocol error occurs.
            OSError: If a network error occurs.
        """
        self._store(uids, "+FLAGS.SILENT", flags)

    def remove_flags(self, uids: list[int], flags: set[str]) -> None:
        """Remove flags from mails in the selected folder.

        Args:
            uids: UIDs of the mails to modify.
            flags: Flags to remove, e.g. {"\\Seen"}.

        Raises:
            RuntimeError: If not connected, if no folder is selected, or if
                the server rejects the operation.
            imaplib.IMAP4.error: If a protocol error occurs.
            OSError: If a network error occurs.
        """
        self._store(uids, "-FLAGS.SILENT", flags)

    def expunge(self, uids: list[int]):
        """Permanently delete mails that are flagged as \\Deleted.

        Only the given UIDs are expunged. Mails must have been flagged with
        \\Deleted first (see add_flags). This cannot be undone.

        Args:
            uids: UIDs of the mails to permanently delete.

        Raises:
            RuntimeError: If not connected, if no folder is selected, if the
                server does not support UIDPLUS, or if the server rejects it.
            imaplib.IMAP4.error: If a protocol error occurs.
            OSError: If a network error occurs.
        """
        if not uids:
            return
                
        connection, _folder = self._require_selected_folder()

        if "UIDPLUS" not in connection.capabilities:
            raise RuntimeError(
                "The server does not support UIDPLUS; refusing to expunge the "
                "whole folder."
            )
        
        uid_set = ",".join(str(uid) for uid in uids)

        try:
            status, data = connection.uid("EXPUNGE", uid_set)
        except (IMAP4.error, OSError):
            logger.exception("A problem occurred while expunging mails: %s", uid_set)
            raise

        self._check_ok(status, data, "Expunge")

    def append(self, folder: str, message: bytes, flags: set[str] | None = None) -> int | None:
        """Upload a mail to a folder, e.g. to save a draft.

        Args:
            folder: Name of the destination folder.
            message: The complete mail in bytes (headers and body).
            flags: Flags to set on the new mail, e.g. {"\\Draft"} or {"\\Seen"}.

        Returns:
            The UID of the new mail, if the server reports it (requires the
            UIDPLUS extension). None otherwise.

        Raises:
            RuntimeError: If not connected or if the server rejects the mail.
            imaplib.IMAP4.error: If a protocol error occurs.
            OSError: If a network error occurs.
        """
        connection = self._require_connection()

        flag_list = f"({' '.join(flags)})" if flags else None

        try:
            status, data = connection.append(self._quote(folder), flag_list, None, message)
        except (IMAP4.error, OSError):
            logger.exception("A problem occurred while appending a mail to %s", folder)
            raise

        self._check_ok(status, data, "Append")

        match = self._append_pattern.search(data[0] or b"")
        return int(match.group(1)) if match else None

    def select_folder(self, folder: str, readonly: bool = False) -> FolderStatus:
        """Select a folder so that mails inside it can be searched and modified.

        Args:
            folder: Name of the folder, e.g. "INBOX".
            readonly: If True, the folder is opened in read-only mode and no
                changes (flags, moves, expunges) are allowed.

        Returns:
            The number of mails in the folder and its UIDVALIDITY. If the
            UIDVALIDITY changes between two selections, previously stored UIDs
            for this folder are no longer valid.

        Raises:
            RuntimeError: If not connected, if the server rejects the folder or
                if it does not report UIDVALIDITY.
            imaplib.IMAP4.error: If a protocol error occurs.
            OSError: If a network error occurs.
        """
        connection = self._require_connection()

        #A failed select leaves no folder selected on the server
        self._selected_folder = None

        try:
            status, data = connection.select(self._quote(folder), readonly=readonly)

        except (IMAP4.error, OSError):
            logger.exception("A problem occurred while selecting folder %s", folder)
            raise

        self._check_ok(status, data, "Select")

        self._selected_folder = folder
        self._readonly = readonly

        _, validity_data = connection.response("UIDVALIDITY")
        if not validity_data or validity_data[0] is None:
            raise RuntimeError(f"The server did not report UIDVALIDITY for {folder}.")

        if not data or data[0] is None:
            raise RuntimeError(f"The server did not report the message count for {folder}.")

        message_count = int(data[0])
        uid_validity = int(validity_data[0])

        return FolderStatus(
            message_count=message_count,
            uid_validity=uid_validity,
        )

    def list_folders(self) -> list[FolderInfo]:
        """List all folders in the account.

        Returns:
            The folders reported by the server, with their attributes
            (e.g. "\\Trash", "\\Drafts") and hierarchy delimiter.

        Raises:
            RuntimeError: If not connected or if the server rejects the command.
            imaplib.IMAP4.error: If a protocol error occurs.
            OSError: If a network error occurs.
        """
        connection = self._require_connection()

        try:
            status, data = connection.list()
        except (IMAP4.error, OSError):
            logger.exception("A problem occurred while listing folders")
            raise

        self._check_ok(status, data, "List")

        folders: list[FolderInfo] = []
        for item in data:
            if not item:
                continue

            # Names with special characters may come as a separate literal.
            if isinstance(item, tuple):
                line, literal_name = item
            else:
                line, literal_name = item, None

            match = _LIST_PATTERN.match(line)
            if match is None:
                logger.warning("Skipping unparseable LIST line: %r", item)
                continue

            if literal_name is not None:
                name = literal_name.decode()
            else:
                name = _unquote(match.group("name").decode())

            delimiter_raw = match.group("delimiter").decode()
            delimiter = None if delimiter_raw == "NIL" else _unquote(delimiter_raw)
            attributes = frozenset(match.group("attributes").decode().split())

            folders.append(FolderInfo(name, attributes, delimiter))

        return folders

    def find_special_folder(self, attribute: str) -> str | None:
        """Find the folder marked with a special-use attribute.

        Args:
            attribute: The attribute to look for, e.g. "\\Trash", "\\Drafts",
                "\\Sent" or "\\Junk".

        Returns:
            The name of the folder, or None if the server does not mark any
            folder with that attribute.

        Raises:
            Same as list_folders().
        """
        wanted = attribute.lower()
        for folder in self.list_folders():
            if any(attr.lower() == wanted for attr in folder.attributes):
                return folder.name
        return None
#endregion
#region "Private Methods"
    def _require_connection(self) -> IMAP4:
        """Return the active connection, or raise if not connected."""
        connection = self._connection
        if connection is None:
            raise RuntimeError("Not connected to the IMAP server.")
        return connection

    def _require_selected_folder(self) -> tuple[IMAP4, str]:
        connection = self._connection
        if connection is None:
            raise RuntimeError("Not connected to the IMAP server.")
        if self._selected_folder is None:
            raise RuntimeError("No folder selected. Call select_folder() first.")
        return connection, self._selected_folder

    def _quote(self, folder: str) -> str:
        """Quote a folder name so it is sent as a single IMAP argument"""
        escaped = folder.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _check_ok(self, status: str, data: list, action: str) -> None:
        """Raise if an IMAP response is not OK."""
        if status != "OK":
            raise RuntimeError(f"{action} failed: {data}")

    def _store(self, uids: list[int], operation: str, flags: set[str]) -> None:
        """Run a UID store command with the given operation flags."""

        if not uids:
            return

        connection, _ = self._require_selected_folder()

        uid_set = ",".join(str(uid) for uid in uids)
        flag_list = f"({' '.join(flags)})"

        try:
            status, data = connection.uid("STORE", uid_set, operation, flag_list)
        except (IMAP4.error, OSError):
            logger.exception(
                "A problem occurred while updating flags %s on mails %s",
                flag_list, uid_set,)
            raise

        self._check_ok(status, data, "Store")

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
#endregion

#endregion
