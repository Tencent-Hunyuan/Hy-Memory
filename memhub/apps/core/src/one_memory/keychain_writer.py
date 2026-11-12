"""Private pipe protocol for one bounded Keychain write."""

import json
import sys

import keyring

from one_memory.secrets import keychain_service_name


def main() -> int:
    try:
        raw = sys.stdin.read(12_289)
        if not raw or len(raw) > 12_288:
            return 1
        body = json.loads(raw)
        if set(body) != {"reference", "value"}:
            return 1
        reference, value = body["reference"], body["value"]
        if not isinstance(reference, str) or not reference or len(reference) > 2048:
            return 1
        if not isinstance(value, str) or not value or len(value) > 8192:
            return 1
        keyring.set_password(keychain_service_name(), reference, value)
        sys.stdout.write(json.dumps({"stored": True}))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
