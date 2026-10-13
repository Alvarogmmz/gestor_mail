"""Module that contains the functionalities of the email service."""

import logging
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from ..clients.imap_client import ImapClient
from ..models.mail import Mail

logger = logging.getLogger(__name__)

TRASH_ATTRIBUTE = "\\Trash"
SEEN_FLAG = "\\Seen"
DELETED_FLAG = "\\Deleted"
DRAFTS_ATTRIBUTE = "\\Drafts"
DRAFT_FLAG = "\\Draft"


class MailHelper:
    """Actions on existing mails: search, read, move, trash, delete and flags.

    Every method receives the folder it works on and selects it itself, so
    calls are independent from each other. The client must already be
    connected; opening and closing the connection is the caller's job.
    """
    def __init__(self, client: ImapClient, default_limit: int = 50, sender: str | None = None):

        self._client = client
        self._sender = sender
        self._default_limit = default_limit
        self._trash_folder: str | None = None
        self._special_folders: dict[str, str] = {}

#region "Public Methods"
    def search(self, folder, *criteria, limit: int | None = None, headers_only: bool = True,):
        """Search mails in a folder, newest first.

        Args:
            folder: Folder to search in, e.g. "INBOX".
            *criteria: IMAP search keys, e.g. "UNSEEN" or 'FROM "a@b.com"'.
                All mails are returned if none are given.
            limit: Maximum number of mails to return. Defaults to the
                service's default limit.
            headers_only: If True, bodies are not downloaded. Use get_mail()
                to read a full mail.

        Returns:
            The matching mails, newest first, up to the limit.

        Raises:
            ValueError: If the limit is not a positive number.
            RuntimeError: If the client is not connected or the server
                rejects the operation.
        """
        limit = self._resolve_limit(limit)

        #Select the folder and search the mail uids
        self._client.select_folder(folder, readonly=True)
        uids = self._client.search(*criteria)

        newest_uids = sorted(uids)[-limit:]
        mails = self._client.fetch(newest_uids, headers_only=headers_only)
        return sorted(mails, key=lambda mail: mail.uid, reverse = True)

    def get_mail(self, folder: str, uid: int) -> Mail:
        """Get a single mail with its full body and attachments.

        Args:
            folder: Folder where the mail is.
            uid: UID of the mail inside that folder.

        Returns:
            The complete mail.

        Raises:
            LookupError: If there is no mail with that UID in the folder.
            RuntimeError: If the client is not connected or the server
                rejects the operation.
        """
        self._client.select_folder(folder, readonly=True)

        mails = self._client.fetch([uid])
        if not mails:
            raise LookupError(f"No mail with UID {uid} in folder {folder}")
        return mails[0]
    
    def move(self, folder: str, uids: list[int], target_folder:str) -> None:
        """Move mails from one folder to another.

        Args:
            folder: Folder where the mails are now.
            uids: UIDs of the mails inside that folder.
            target_folder: Destination folder.

        Raises:
            ValueError: If the source and destination folders are the same.
            RuntimeError: If the client is not connected or the server
                rejects the operation.
        """
        if not uids:
            return 
        if folder == target_folder:
            raise ValueError("Source and target folders are the same.")

        self._client.select_folder(folder)
        self._client.move(uids, target_folder)


    def move_to_trash(self, folder: str, uids: list[int]) -> None:
        """Move mails to the trash. They can be restored later.

        Args:
            folder: Folder where the mails are now.
            uids: UIDs of the mails inside that folder.

        Raises:
            ValueError: If the mails are already in the trash.
            RuntimeError: If the trash folder cannot be found, the client is
                not connected or the server rejects the operation.
        """
        if not uids:
            return
        trash = self._get_trash_folder()
        if folder == trash:
            raise ValueError("The mails are already in the trash.")
        self.move(folder, uids, trash)
        

    def restore_from_trash(self, uids: list[int], target_folder: str = "INBOX") -> None:
        """Move mails from the trash back to a folder.

        IMAP does not remember where a mail was before being trashed, so the
        destination must be given. Defaults to the inbox.

        Args:
            uids: UIDs of the mails inside the trash.
            target_folder: Folder to restore the mails to.

        Raises:
            RuntimeError: If the trash folder cannot be found, the client is
                not connected or the server rejects the operation.
        """
        if not uids:
            return 

        trash = self._get_trash_folder()
        if target_folder == trash:
            raise ValueError("You are trying to move mails from trash folder to trash folder.")
        self.move(trash, uids, target_folder)

    def delete_permanently(self, uids: list[int]) -> None:
        """Permanently delete mails from the trash. This cannot be undone.

        Only mails already in the trash can be deleted, as a safety measure.
        Use move_to_trash() first.

        Args:
            uids: UIDs of the mails inside the trash.

        Raises:
            RuntimeError: If the trash folder cannot be found, the client is
                not connected or the server rejects the operation.
        """
        self._remove_permanently(self._get_special_folder(TRASH_ATTRIBUTE), uids)
        logger.info("Permanently deleted mails %s", uids)


    def _remove_permanently(self, folder: str, uids: list[int]) -> None:
        """Flag mails as deleted and expunge them from a folder."""
        if not uids:
            return

        self._client.select_folder(folder)
        self._client.add_flags(uids, {DELETED_FLAG})
        self._client.expunge(uids)

    def mark_as_read(self, folder: str, uids: list[int]) -> None:
        """Mark mails as read.

        Args:
            folder: Folder where the mails are.
            uids: UIDs of the mails inside that folder.
        """
        self._set_flag(folder, uids, SEEN_FLAG, enabled=True)

    def mark_as_unread(self, folder: str, uids: list[int]) -> None:
        """Mark mails as unread.

        Args:
            folder: Folder where the mails are.
            uids: UIDs of the mails inside that folder.
        """
        self._set_flag(folder, uids, SEEN_FLAG, enabled=False)

    def list_drafts(self, limit: int | None = None) -> list[Mail]:
        """List the drafts, newest first.

        Args:
            limit: Maximum number of drafts to return. Defaults to the
                service's default limit.

        Returns:
            The drafts, with headers only. Use get_mail() to read one fully.
        """
        return self.search(self._get_special_folder(DRAFTS_ATTRIBUTE), limit=limit)


    def create_draft(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
    ) -> int | None:
        """Create a new draft in the drafts folder. Nothing is sent.

        Args:
            to: Recipient addresses.
            subject: Subject of the mail.
            body: Plain-text body of the mail.
            cc: Carbon-copy addresses, if any.

        Returns:
            The UID of the new draft, or None if the server does not report it.

        Raises:
            RuntimeError: If the drafts folder cannot be found, the client is
                not connected or the server rejects the operation.
        """
        message = self._build_message(to, subject, body, cc)
        drafts = self._get_special_folder(DRAFTS_ATTRIBUTE)
        return self._client.append(drafts, message.as_bytes(), {DRAFT_FLAG})


    def update_draft(
        self,
        uid: int,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
    ) -> int | None:
        """Replace an existing draft with a new version.

        IMAP cannot modify mails, so the new version is uploaded first and the
        old one is removed afterwards. The draft gets a new UID.

        Args:
            uid: UID of the draft to replace.
            to: Recipient addresses.
            subject: Subject of the mail.
            body: Plain-text body of the mail.
            cc: Carbon-copy addresses, if any.

        Returns:
            The UID of the new version, or None if the server does not report it.

        Raises:
            LookupError: If there is no draft with that UID.
            RuntimeError: If the drafts folder cannot be found, the client is
                not connected or the server rejects the operation.
        """
        drafts = self._get_special_folder(DRAFTS_ATTRIBUTE)

        self._client.select_folder(drafts, readonly=True)
        if not self._client.search(f"UID {uid}"):
            raise LookupError(f"No draft with UID {uid}.")

        new_uid = self.create_draft(to, subject, body, cc)
        self._remove_permanently(drafts, [uid])
        return new_uid


    def discard_draft(self, uids: list[int]) -> None:
        """Move drafts to the trash. They can be restored later.

        Args:
            uids: UIDs of the drafts to discard.

        Raises:
            RuntimeError: If the drafts or trash folder cannot be found, the
                client is not connected or the server rejects the operation.
        """
        self.move_to_trash(self._get_special_folder(DRAFTS_ATTRIBUTE), uids)

    def get_uid_validity(self, folder: str) -> int:
        """Return the UIDVALIDITY of a folder.

        If it changes, the server has renumbered the folder and any stored UIDs
        for it must be discarded.

        Args:
            folder: Name of the folder.

        Returns:
            The current UIDVALIDITY of the folder.

        Raises:
            RuntimeError: If the client is not connected or the server rejects
                the operation.
        """
        return self._client.select_folder(folder, readonly=True).uid_validity


    def get_highest_uid(self, folder: str) -> int | None:
        """Return the highest UID in a folder, i.e. the most recent mail.

        Args:
            folder: Name of the folder.

        Returns:
            The highest UID, or None if the folder is empty.

        Raises:
            RuntimeError: If the client is not connected or the server rejects
                the operation.
        """
        self._client.select_folder(folder, readonly=True)
        return max(self._client.search(), default=None)


    def fetch_new(
        self,
        folder: str,
        after_uid: int,
        limit: int | None = None,
        unread_only: bool = False,
    ) -> list[Mail]:
        """Get the mails that arrived after a given UID, oldest first.

        Mails are downloaded in full (body and attachments) and are not marked
        as read. If there are more new mails than the limit, only the oldest
        ones are returned; the rest can be fetched in a later call.

        Args:
            folder: Folder to look in, e.g. "INBOX".
            after_uid: Only mails with a higher UID are returned.
            limit: Maximum number of mails to return. Defaults to the
                service's default limit.
            unread_only: If True, mails already read are left out and do not
                count towards the limit.

        Returns:
            The new mails, oldest first, up to the limit.

        Raises:
            ValueError: If the limit is not a positive number.
            RuntimeError: If the client is not connected or the server rejects
                the operation.
        """
        limit = self._resolve_limit(limit)

        self._client.select_folder(folder, readonly=True)

        # "UID n:*" always includes the highest UID in the folder, even when it
        # is lower than n, so the result must be filtered.
        criteria = [f"UID {after_uid + 1}:*"]
        if unread_only:
            criteria.append("UNSEEN")
        uids = self._client.search(*criteria)
        new_uids = sorted(uid for uid in uids if uid > after_uid)[:limit]

        mails = self._client.fetch(new_uids, headers_only=False)
        return sorted(mails, key=lambda mail: mail.uid)
#endregion

#region "Private Methods"

    def _resolve_limit(self, limit: int | None) -> int:
        """Return the given limit, or the default one, checking it is positive."""
        limit = self._default_limit if limit is None else limit
        if limit <= 0:
            raise ValueError("The limit must be a positive number.")
        return limit
    
    def _build_message(
        self, to: list[str], subject: str, body: str, cc: list[str] | None
    ) -> EmailMessage:
        """Build a plain-text mail ready to be uploaded."""
        message = EmailMessage()
        if self._sender is not None:
            message["From"] = self._sender
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid()
        message.set_content(body)
        return message

    def _get_trash_folder(self) -> str:
        """Return the name of the trash folder, looking it up only once."""
        if self._trash_folder is None:
            trash = self._client.find_special_folder(TRASH_ATTRIBUTE)
            if trash is None:
                raise RuntimeError("Could not find the trash folder on the server.")
            self._trash_folder = trash
        return self._trash_folder

    def _set_flag(self, folder: str, uids: list[int], flag: str, enabled: bool) -> None:
        """Add or remove a flag on mails in a folder."""
        if not uids:
            return

        self._client.select_folder(folder)
        if enabled:
            self._client.add_flags(uids, {flag})
        else:
            self._client.remove_flags(uids, {flag})

    def _get_special_folder(self, attribute: str) -> str:
        """Return the name of a special-use folder, looking it up only once."""
        if attribute not in self._special_folders:
            name = self._client.find_special_folder(attribute)
            if name is None:
                raise RuntimeError(f"Could not find the {attribute} folder on the server.")
            self._special_folders[attribute] = name
        return self._special_folders[attribute]

#endregion

