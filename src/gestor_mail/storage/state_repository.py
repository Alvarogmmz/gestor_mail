"""Persistence of the processing state of each folder: the last UID
processed and the UIDVALIDITY it belongs to."""
import contextlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FolderState:
    """Processing state of a folder.

    Attributes:
        uid_validity: UIDVALIDITY of the folder when the state was saved.
            If the server reports a different one, the state is obsolete.
        last_uid: UID of the last processed mail. 0 means nothing has been
            processed yet.

    Raises:
        ValueError: If uid_validity is not positive or last_uid is negative.
    """
    uid_validity: int
    last_uid: int

    def __post_init__(self) -> None:
        if self.uid_validity < 0:
            raise ValueError("uid_validity must be a positive number.")
        if self.last_uid < 0:
            raise ValueError("last_uid must be a positive number.")

class StateRepository:
    """Stores the processing state of each folder in a JSON file.
    Args:
        path: Location of the JSON file.
    """

    def __init__(self, path: Path):
        self._path = path

    def load(self, folder: str) -> FolderState | None:
        """Load the folder state data from a JSON file.
        Args:
            folder (str): the folder name.
        Returns:
            The stored state, or None if nothing has been stored for this
            folder yet
        Raises:
            RunTimeError: If the state file is corrupted or has an
                unexpected format."""
        folder_data = self._read_all().get(folder)
        if folder_data is None:
            return None
        try:
            return FolderState(
                last_uid=int(folder_data['last_uid']),
                uid_validity=int(folder_data['uid_validity'])
            )
        except (KeyError, ValueError, TypeError) as error:
            raise RuntimeError(f"Invalid state for folder {folder} in {self._path}.") from error

    def save(self, folder:str, state: FolderState) -> None:
        """Save the state of a folder, keeping the state of the others.

            The file is written atomically, so a crash while saving never
            leaves it half-written.

            Args:
                folder: Name of the folder.
                state: State to store.

            Raises:
                ValueError: If the folder name is empty.
                RuntimeError: If the existing state file is corrupted or has an
                    unexpected format.
        """
        if not folder:
            raise ValueError("The folder name cannot be empty")

        data = self._read_all()
        data[folder] = asdict(state)
        self._write_all(data)

    def _read_all(self) -> dict:
        """Read the whole state file.

        Returns:
            The content of the file, or an empty dict if it does not exist.

        Raises:
            RuntimeError: If the file cannot be read, is not valid UTF-8 or
                JSON, or does not contain a JSON object.
            TypeError: If the sate file has an unexpected format
        """
        try:
            with self._path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError(f"The state file {self._path} is corrupted.") from error
        except OSError as error:
            raise RuntimeError(f"Could not read the state file {self._path}.") from error

        if not isinstance(data, dict):
            raise TypeError(f"The state file {self._path} has an unexpected format.")
        return data

    def _write_all(self, data: dict) -> None:
        """Write the whole state file atomically through a temporary file.

        The temporary file is always removed, whether the write succeeds
        or not.

        Args:
            data: Complete content to write.

        Raises:
            RuntimeError: If the file or its folder cannot be written.
        """

        temp_path = self._path.with_name(self._path.name + ".tmp")

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=4)
            os.replace(temp_path, self._path)
        except OSError as error:
            raise RuntimeError(f"Could not write the state file {self._path}.") from error
        finally:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)

