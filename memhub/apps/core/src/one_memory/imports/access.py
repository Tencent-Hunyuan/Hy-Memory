import errno
import hashlib
import os
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

DEFAULT_MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024 * 1024
SENSITIVE_HOME_DIRECTORIES = {
    "Desktop",
    "Documents",
    "Downloads",
    "Library/Mobile Documents",
}


class SourceAccessErrorCode(StrEnum):
    GRANT_REQUIRED = "grant_required"
    ROOT_NOT_DIRECTORY = "root_not_directory"
    SENSITIVE_ROOT = "sensitive_root"
    PATH_OUTSIDE_ROOT = "path_outside_root"
    SYMLINK_NOT_ALLOWED = "symlink_not_allowed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    NOT_REGULAR_FILE = "not_regular_file"
    FILE_TOO_LARGE = "file_too_large"
    BUDGET_EXCEEDED = "budget_exceeded"
    SOURCE_IDENTITY_CHANGED = "source_identity_changed"
    SOURCE_PERMISSION_CHANGED = "source_permission_changed"
    SOURCE_CANDIDATE_CONFIRMATION_REQUIRED = "source_candidate_confirmation_required"


class SourceAccessError(RuntimeError):
    def __init__(self, code: SourceAccessErrorCode) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AuthorizedSourceFile:
    source_id: str
    root: Path
    path: Path
    relative_path: Path
    max_bytes: int
    file_identity: str
    size: int
    modified_ns: int
    changed_ns: int
    # Source-specific change token (for example Cursor's WAL identity). It is
    # cursor metadata only; modified_ns remains the real filesystem mtime used
    # as a fallback conversation timestamp.
    cursor_version: str | None = None
    root_identity: str | None = None
    requested_root: Path | None = None
    access_check: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    @property
    def safe_id(self) -> str:
        identity = f"{self.source_id}\0{self.relative_path.as_posix()}"
        return hashlib.sha256(identity.encode()).hexdigest()

    def admit(self) -> "AuthorizedSourceFile":
        """Check the grant once for this file; the returned copy's opens do not repeat it.

        Opens still refuse symlinks, any inode other than the listed one, and a
        replaced root. Only the grant lookup is not repeated per open.
        """
        if self.access_check is not None:
            self.access_check()
        return replace(self, access_check=None)

    def open_binary(self) -> BinaryIO:
        if self.access_check is not None:
            self.access_check()
        return _open_relative_file(self)


def _open_relative_file(source: AuthorizedSourceFile) -> BinaryIO:
    if not _supports_descriptor_relative_open():
        raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE)
    relative_parts = source.relative_path.parts
    if (
        source.relative_path.is_absolute()
        or not relative_parts
        or any(part in {"", ".", ".."} for part in relative_parts)
    ):
        raise SourceAccessError(SourceAccessErrorCode.PATH_OUTSIDE_ROOT)

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    child_directory_flags = directory_flags | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directories: list[int] = []
    directory_edges: list[tuple[int, str, os.stat_result]] = []
    descriptor: int | None = None
    failure: BaseException | None = None
    try:
        requested_root = source.requested_root or source.root
        try:
            root_descriptor = os.open(requested_root, directory_flags)
        except (OSError, TypeError, NotImplementedError) as error:
            if isinstance(error, OSError) and error.errno == errno.ENOTDIR:
                raise SourceAccessError(SourceAccessErrorCode.ROOT_NOT_DIRECTORY) from None
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
        directories.append(root_descriptor)
        try:
            requested_details = os.fstat(root_descriptor)
        except (OSError, TypeError, NotImplementedError):
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
        if not stat.S_ISDIR(requested_details.st_mode):
            raise SourceAccessError(SourceAccessErrorCode.ROOT_NOT_DIRECTORY)

        canonical_details = requested_details
        if requested_root != source.root:
            try:
                canonical_descriptor = os.open(source.root, directory_flags)
            except (OSError, TypeError, NotImplementedError) as error:
                if isinstance(error, OSError) and error.errno == errno.ENOTDIR:
                    raise SourceAccessError(SourceAccessErrorCode.ROOT_NOT_DIRECTORY) from None
                raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
            directories.append(canonical_descriptor)
            try:
                canonical_details = os.fstat(canonical_descriptor)
            except (OSError, TypeError, NotImplementedError):
                raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
            if not stat.S_ISDIR(canonical_details.st_mode):
                raise SourceAccessError(SourceAccessErrorCode.ROOT_NOT_DIRECTORY)
            if (requested_details.st_dev, requested_details.st_ino) != (
                canonical_details.st_dev,
                canonical_details.st_ino,
            ):
                raise SourceAccessError(SourceAccessErrorCode.SOURCE_IDENTITY_CHANGED)
        if source.root_identity is not None and not _root_identity_matches_details(
            source.root,
            canonical_details,
            source.root_identity,
        ):
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_IDENTITY_CHANGED)

        parent_descriptor = root_descriptor
        for component in relative_parts[:-1]:
            try:
                child_descriptor = os.open(
                    component,
                    child_directory_flags,
                    dir_fd=parent_descriptor,
                )
            except OSError as error:
                raise _relative_open_error(parent_descriptor, component, error) from None
            except (TypeError, NotImplementedError):
                raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
            directories.append(child_descriptor)
            try:
                child_details = os.fstat(child_descriptor)
            except (OSError, TypeError, NotImplementedError):
                raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
            if not stat.S_ISDIR(child_details.st_mode):
                raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE)
            directory_edges.append((parent_descriptor, component, child_details))
            parent_descriptor = child_descriptor

        try:
            descriptor = os.open(relative_parts[-1], file_flags, dir_fd=parent_descriptor)
        except OSError as error:
            raise _relative_open_error(parent_descriptor, relative_parts[-1], error) from None
        except (TypeError, NotImplementedError):
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
        try:
            details = os.fstat(descriptor)
        except (OSError, TypeError, NotImplementedError):
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
        if not stat.S_ISREG(details.st_mode):
            raise SourceAccessError(SourceAccessErrorCode.NOT_REGULAR_FILE)
        if not _same_file(
            details,
            file_identity=source.file_identity,
            changed_ns=source.changed_ns,
        ):
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE)
        if details.st_size > source.max_bytes:
            raise SourceAccessError(SourceAccessErrorCode.FILE_TOO_LARGE)
        _check_root_identity(requested_root, source.root_identity)
        for ancestor_descriptor, component, opened_details in directory_edges:
            try:
                current_details = os.stat(
                    component,
                    dir_fd=ancestor_descriptor,
                    follow_symlinks=False,
                )
            except (OSError, TypeError, NotImplementedError) as error:
                if isinstance(error, OSError):
                    raise _relative_open_error(ancestor_descriptor, component, error) from None
                raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
            if stat.S_ISLNK(current_details.st_mode):
                raise SourceAccessError(SourceAccessErrorCode.SYMLINK_NOT_ALLOWED)
            if (current_details.st_dev, current_details.st_ino) != (
                opened_details.st_dev,
                opened_details.st_ino,
            ):
                raise SourceAccessError(SourceAccessErrorCode.SOURCE_IDENTITY_CHANGED)
        try:
            stream = os.fdopen(descriptor, "rb")
        except (OSError, TypeError, ValueError):
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
        descriptor = None
        if not _close_descriptors(directories):
            directories.clear()
            with suppress(OSError):
                stream.close()
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE)
        directories.clear()
        return stream
    except BaseException as error:
        failure = error
        raise
    finally:
        descriptors = ([descriptor] if descriptor is not None else []) + list(reversed(directories))
        for owned in descriptors:
            try:
                os.close(owned)
            except OSError:
                if failure is None:
                    raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None


def _supports_descriptor_relative_open() -> bool:
    return (
        os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and callable(getattr(os, "fstat", None))
        and callable(getattr(os, "fdopen", None))
        and all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"))
    )


def _relative_open_error(
    parent_descriptor: int, component: str, error: OSError
) -> SourceAccessError:
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        try:
            details = os.stat(component, dir_fd=parent_descriptor, follow_symlinks=False)
        except (OSError, TypeError, NotImplementedError):
            return SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE)
        if stat.S_ISLNK(details.st_mode):
            return SourceAccessError(SourceAccessErrorCode.SYMLINK_NOT_ALLOWED)
    return SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE)


def _close_descriptors(descriptors: list[int]) -> bool:
    closed = True
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            closed = False
    return closed


@dataclass(frozen=True)
class AuthorizedSourceRoot:
    source_id: str
    root: Path
    max_file_bytes: int = DEFAULT_MAX_SOURCE_FILE_BYTES
    root_identity: str | None = None
    requested_root: Path | None = None
    access_check: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def grant(
        cls,
        *,
        source_id: str,
        root: Path,
        user_confirmed: bool,
        home: Path,
        max_file_bytes: int = DEFAULT_MAX_SOURCE_FILE_BYTES,
    ) -> "AuthorizedSourceRoot":
        if not user_confirmed:
            raise SourceAccessError(SourceAccessErrorCode.GRANT_REQUIRED)
        try:
            resolved = root.expanduser().resolve(strict=True)
        except OSError:
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
        if not resolved.is_dir():
            raise SourceAccessError(SourceAccessErrorCode.ROOT_NOT_DIRECTORY)
        if _is_sensitive_home_root(resolved, home.resolve()):
            raise SourceAccessError(SourceAccessErrorCode.SENSITIVE_ROOT)
        return cls(
            source_id=source_id,
            root=resolved,
            max_file_bytes=max_file_bytes,
            root_identity=source_root_identity(root),
            requested_root=root.expanduser().absolute(),
        )

    def check_root(self) -> None:
        if self.access_check is not None:
            self.access_check()
        _check_root_identity(self.requested_root or self.root, self.root_identity)

    def listed_file(self, candidate: Path) -> AuthorizedSourceFile:
        """Describe a file met while walking this root, from its lstat alone.

        Only for paths the walk reached through non-symlink directories. The
        caller has already run check_root() for the walk.
        """
        try:
            relative = candidate.relative_to(self.root)
        except ValueError:
            raise SourceAccessError(SourceAccessErrorCode.PATH_OUTSIDE_ROOT) from None
        try:
            details = os.lstat(candidate)
        except OSError:
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
        if stat.S_ISLNK(details.st_mode):
            raise SourceAccessError(SourceAccessErrorCode.SYMLINK_NOT_ALLOWED)
        if not stat.S_ISREG(details.st_mode):
            raise SourceAccessError(SourceAccessErrorCode.NOT_REGULAR_FILE)
        if details.st_size > self.max_file_bytes:
            raise SourceAccessError(SourceAccessErrorCode.FILE_TOO_LARGE)
        return AuthorizedSourceFile(
            source_id=self.source_id,
            root=self.root,
            path=candidate,
            relative_path=relative,
            max_bytes=self.max_file_bytes,
            file_identity=f"{details.st_dev}:{details.st_ino}",
            size=details.st_size,
            modified_ns=details.st_mtime_ns,
            changed_ns=_changed_ns(details),
            root_identity=self.root_identity,
            requested_root=self.requested_root,
            access_check=self.access_check,
        )

    def authorize_file(self, candidate: Path) -> AuthorizedSourceFile:
        if self.access_check is not None:
            self.access_check()
        _check_root_identity(self.requested_root or self.root, self.root_identity)
        logical = candidate if candidate.is_absolute() else self.root / candidate
        if logical.is_symlink():
            raise SourceAccessError(SourceAccessErrorCode.SYMLINK_NOT_ALLOWED)
        try:
            resolved = logical.resolve(strict=True)
        except OSError:
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
        if not resolved.is_relative_to(self.root):
            raise SourceAccessError(SourceAccessErrorCode.PATH_OUTSIDE_ROOT)
        relative = resolved.relative_to(self.root)
        _reject_symlink_components(self.root, relative)
        try:
            details = resolved.stat()
        except OSError:
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
        if not stat.S_ISREG(details.st_mode):
            raise SourceAccessError(SourceAccessErrorCode.NOT_REGULAR_FILE)
        if details.st_size > self.max_file_bytes:
            raise SourceAccessError(SourceAccessErrorCode.FILE_TOO_LARGE)
        return AuthorizedSourceFile(
            source_id=self.source_id,
            root=self.root,
            path=resolved,
            relative_path=relative,
            max_bytes=self.max_file_bytes,
            file_identity=f"{details.st_dev}:{details.st_ino}",
            size=details.st_size,
            modified_ns=details.st_mtime_ns,
            changed_ns=_changed_ns(details),
            root_identity=self.root_identity,
            requested_root=self.requested_root,
            access_check=self.access_check,
        )


def _root_details(root: Path) -> tuple[Path, os.stat_result]:
    try:
        resolved = root.expanduser().resolve(strict=True)
        details = resolved.stat()
    except (OSError, RuntimeError):
        raise SourceAccessError(SourceAccessErrorCode.SOURCE_UNAVAILABLE) from None
    if not stat.S_ISDIR(details.st_mode):
        raise SourceAccessError(SourceAccessErrorCode.ROOT_NOT_DIRECTORY)
    return resolved, details


def _changed_ns(details: os.stat_result) -> int:
    """Stable creation time when the platform exposes one.

    ext4 can recycle an inode number immediately after an unlink, so
    ``st_dev:st_ino`` is not enough to tell a replacement from the file that was
    authorised. macOS reports birth time, which a newly created replacement
    cannot share. Linux has no portable birth-time field here, so zero means
    "unavailable" and callers fall back to the existing inode identity.
    """
    birthtime_ns = getattr(details, "st_birthtime_ns", None)
    if birthtime_ns is None:
        birthtime = getattr(details, "st_birthtime", None)
        birthtime_ns = None if birthtime is None else int(birthtime * 1_000_000_000)
    return int(birthtime_ns) if birthtime_ns is not None else 0


def _same_file(details: os.stat_result, *, file_identity: str, changed_ns: int) -> bool:
    if f"{details.st_dev}:{details.st_ino}" != file_identity:
        return False
    return changed_ns == 0 or _changed_ns(details) == changed_ns


def _hash_root(resolved: Path, details: os.stat_result, device: int | None) -> str:
    prefix = "" if device is None else f"{device}:"
    identity = (
        f"{resolved}\0{prefix}{details.st_ino}:{details.st_mode}:{details.st_uid}:{details.st_gid}"
    )
    return "root:v2:" + hashlib.sha256(identity.encode()).hexdigest()


def _root_identity_matches_details(resolved: Path, details: os.stat_result, expected: str) -> bool:
    return _hash_root(resolved, details, None) == expected


def source_root_identity(root: Path) -> str:
    """Hash location and directory authority using metadata only, not history contents.

    st_dev is excluded: macOS renumbers APFS volumes across reboots, and a
    device-bound identity would silently revoke every grant after a restart.
    """
    resolved, details = _root_details(root)
    return _hash_root(resolved, details, None)


def root_identity_matches(root: Path, expected: str) -> bool:
    resolved, details = _root_details(root)
    return _root_identity_matches_details(resolved, details, expected)


def _check_root_identity(root: Path, expected: str | None) -> None:
    if expected is not None and not root_identity_matches(root, expected):
        raise SourceAccessError(SourceAccessErrorCode.SOURCE_IDENTITY_CHANGED)


def safe_project_label(value: str | None) -> str | None:
    if not value:
        return None
    label = Path(value).name.strip()
    if not label:
        return None
    return "".join(character for character in label if character.isprintable())[:128]


def opaque_workspace_id(source_id: str, workspace: str | None) -> str | None:
    if not workspace:
        return None
    if len(workspace) > 4_096 or any(ord(character) < 32 for character in workspace):
        return None
    # Workspace is an opaque identity, not a filesystem target. Hash the
    # caller's exact value without expansion, resolution, stat, open, or join.
    digest = hashlib.sha256(f"{source_id}\0{workspace}".encode()).hexdigest()
    return f"workspace:v1:{digest}"


def opaque_conversation_error_id(source_id: str, conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    digest = hashlib.sha256(f"{source_id}\0{conversation_id}".encode()).hexdigest()
    return f"conversation:v1:{digest}"


def _is_sensitive_home_root(root: Path, home: Path) -> bool:
    return any(
        root == home / relative or root.is_relative_to(home / relative)
        for relative in SENSITIVE_HOME_DIRECTORIES
    )


def _reject_symlink_components(root: Path, relative: Path) -> None:
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise SourceAccessError(SourceAccessErrorCode.SYMLINK_NOT_ALLOWED)
