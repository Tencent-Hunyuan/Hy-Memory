"""Private pipe protocol for one bounded read, never a server or file export."""

import json
import sys

from one_memory.secrets import (
    SecretStoreUnavailableError,
    _macos_password_without_prompt,
)


def main() -> int:
    try:
        reference = sys.stdin.read(2049)
        if not reference or len(reference) > 2048:
            return 1
        if sys.platform == "darwin":
            value = _macos_password_without_prompt(reference)
        else:
            import keyring

            from one_memory.secrets import keychain_service_name

            value = keyring.get_password(keychain_service_name(), reference)
        sys.stdout.write(json.dumps({"value": value}))
        return 0
    except SecretStoreUnavailableError as error:
        # Why the read failed is the whole point of the read: "the user has not
        # allowed this entry" and "there is no store" need different words in
        # the UI. The code is one of a fixed set and carries no credential.
        sys.stdout.write(json.dumps({"error": str(error.code)}))
        return 1
    except Exception:
        # Neither OS errors nor credential values may escape into Core logs.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
