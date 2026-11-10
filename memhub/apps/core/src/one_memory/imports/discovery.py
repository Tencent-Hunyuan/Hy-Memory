import hashlib
import os
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from one_memory.imports.access import (
    AuthorizedSourceFile,
    AuthorizedSourceRoot,
    SourceAccessError,
    SourceAccessErrorCode,
)
from one_memory.imports.async_bridge import iterate_in_worker


@dataclass(frozen=True)
class SourceFileDiscoveryFailure:
    file_key: str
    reason_code: SourceAccessErrorCode


type SourceFileDiscoveryEvent = AuthorizedSourceFile | SourceFileDiscoveryFailure


def discovered_off_loop(
    grant: AuthorizedSourceRoot,
    *,
    suffixes: frozenset[str],
    max_targets: int,
    excluded_directory_names: frozenset[str] = frozenset(),
    directory_predicate: Callable[[Path], bool] | None = None,
) -> AsyncIterator[SourceFileDiscoveryEvent]:
    """The same walk, for callers that have an event loop to keep free.

    Walking a host's data directory means `os.walk` over tens of thousands of
    files on a real machine. Driven straight from a coroutine it held the loop
    for seconds at a time, and for those seconds every recall a host asked for
    was waiting behind a directory listing. Batched through a worker instead,
    so neither the loop nor memory holds the whole walk.
    """
    return iterate_in_worker(
        lambda: discover_source_files(
            grant,
            suffixes=suffixes,
            max_targets=max_targets,
            excluded_directory_names=excluded_directory_names,
            directory_predicate=directory_predicate,
        ),
        scope_key=grant.source_id,
    )


def discover_source_files(
    grant: AuthorizedSourceRoot,
    *,
    suffixes: frozenset[str],
    max_targets: int,
    excluded_directory_names: frozenset[str] = frozenset(),
    directory_predicate: Callable[[Path], bool] | None = None,
) -> Iterator[SourceFileDiscoveryEvent]:
    """Metadata only: one lstat per candidate, no open and no per-file grant lookup.

    The grant and root identity are checked once before the walk. Each listed
    file keeps the grant's check, which runs again when that file is opened;
    the open itself refuses symlinks and any inode other than the one listed.
    `excluded_directory_names` are directory names never entered, at any
    depth under the root, such as the `profiles` container that holds other
    users' Hermes profiles. Any depth, because the granted root may sit above
    the Hermes home (`~/.hermes` inside it is then `.hermes/profiles`).
    """
    grant.check_root()
    inspected = 0
    for directory, child_directories, filenames in os.walk(
        grant.root,
        followlinks=False,
    ):
        current = Path(directory)
        child_directories[:] = sorted(
            child
            for child in child_directories
            if not (current / child).is_symlink()
            and child not in excluded_directory_names
            and (
                directory_predicate is None
                or directory_predicate((current / child).relative_to(grant.root))
            )
        )
        for filename in sorted(filenames):
            if Path(filename).suffix.lower() not in suffixes:
                continue
            if inspected >= max_targets:
                yield SourceFileDiscoveryFailure(
                    file_key=_opaque_candidate_id(
                        grant.source_id,
                        grant.root,
                        Path(directory),
                    ),
                    reason_code=SourceAccessErrorCode.BUDGET_EXCEEDED,
                )
                return
            inspected += 1
            candidate = Path(directory) / filename
            try:
                authorized = grant.listed_file(candidate)
            except SourceAccessError as error:
                yield SourceFileDiscoveryFailure(
                    file_key=_opaque_candidate_id(grant.source_id, grant.root, candidate),
                    reason_code=error.code,
                )
                continue
            yield authorized


def _opaque_candidate_id(source_id: str, root: Path, candidate: Path) -> str:
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError:
        relative = "outside-root"
    return hashlib.sha256(f"{source_id}\0{relative}".encode()).hexdigest()
