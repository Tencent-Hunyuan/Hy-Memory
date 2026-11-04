"""Independent billing credentials live in the OS secret store, never model config."""

import json
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from one_memory.provider_billing_catalog import BillingProvider
from one_memory.secrets import SecretStore


class BillingAuthorizationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: dict[str, SecretStr] = Field(max_length=8)

    @field_validator("fields")
    @classmethod
    def bounded_values(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        if any(
            len(k) > 64
            or len(v.get_secret_value()) > 16384
            or any(c in v.get_secret_value() for c in "\r\n")
            for k, v in value.items()
        ):
            raise ValueError("invalid_billing_fields")
        return value


class BillingAuthorizationStore:
    def __init__(self, data_dir: Path, secrets: SecretStore) -> None:
        self.path = data_dir / "billing-authorizations.json"
        self.secrets = secrets
        self._lock = threading.RLock()
        self._refs: dict[str, dict[str, str]] = (
            json.loads(self.path.read_text()) if self.path.exists() else {}
        )

    def revision(self, profile_id: str, provider: BillingProvider) -> str | None:
        with self._lock:
            entry = self._refs.get(profile_id)
            return entry["ref"] if entry and entry["provider"] == provider.name else None

    def resolve(self, profile_id: str, provider: BillingProvider) -> dict[str, str] | None:
        ref = self.revision(profile_id, provider)
        if ref is None:
            return None
        raw = self.secrets.resolve(ref)
        return json.loads(raw) if raw is not None else None

    def put(
        self, profile_id: str, provider: BillingProvider, value: BillingAuthorizationInput
    ) -> None:
        fields = {
            k: v.get_secret_value().strip()
            for k, v in value.fields.items()
            if v.get_secret_value().strip()
        }
        allowed = {f.name for f in provider.fields}
        if (
            not allowed
            or fields.keys() - allowed
            or any(f.required and f.name not in fields for f in provider.fields)
        ):
            raise ValueError("invalid_billing_fields")
        ref = "billing:" + uuid4().hex
        with self._lock:
            self.secrets.put(ref, json.dumps(fields))
            updated = {**self._refs, profile_id: {"provider": provider.name, "ref": ref}}
            try:
                self._save(updated)
            except Exception:
                # A failed metadata commit must not leave an unreferenced credential.
                self.secrets.delete(ref)
                raise
            old = self._refs.get(profile_id)
            self._refs = updated
            if old:
                self.secrets.delete(old["ref"])

    def delete(self, profile_id: str) -> None:
        with self._lock:
            old = self._refs.get(profile_id)
            if old is None:
                return
            self.secrets.delete(old["ref"])
            updated = {k: v for k, v in self._refs.items() if k != profile_id}
            self._save(updated)
            self._refs = updated

    def _save(self, entries: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(json.dumps(entries, indent=2))
                file.close()
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
