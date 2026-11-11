"""Private pipe protocol for one bounded Keychain deletion."""

import json
import sys
from contextlib import suppress

import keyring
from keyring.errors import PasswordDeleteError

from one_memory.secrets import keychain_service_name


def main() -> int:
    try:
        raw = sys.stdin.read(16_385)
        if not raw or len(raw) > 16_384:
            return 1
        if sys.argv[1:] == ["--many"]:
            body = json.loads(raw)
            references = body.get("references") if isinstance(body, dict) else None
            if (
                not isinstance(references, list)
                or len(references) > 256
                or any(not isinstance(ref, str) or not ref or len(ref) > 2048 for ref in references)
            ):
                return 1
            for reference in references:
                with suppress(PasswordDeleteError):
                    keyring.delete_password(keychain_service_name(), reference)
            sys.stdout.write(json.dumps({"deleted": len(references)}))
        else:
            reference = raw
            with suppress(PasswordDeleteError):
                keyring.delete_password(keychain_service_name(), reference)
            sys.stdout.write(json.dumps({"deleted": True}))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
