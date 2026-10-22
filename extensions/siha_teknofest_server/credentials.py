"""OS credential-store integration owned by the TEKNOFEST extension."""

from __future__ import annotations

from typing import Any


class CredentialStoreError(RuntimeError):
    """The operating-system credential store is missing or unavailable."""


class ServerCredentialStore:
    SERVICE_NAME = "Argestes Ground Station: siha_teknofest_server"

    def __init__(self, keyring_api: Any | None = None) -> None:
        self._keyring_api = keyring_api

    def _api(self):
        if self._keyring_api is None:
            try:
                import keyring
            except Exception as exc:
                raise CredentialStoreError(
                    "İşletim sistemi parola kasası kullanılamıyor. Keyring paketini ve masaüstü parola kasasını kurup açın."
                ) from exc
            self._keyring_api = keyring
        try:
            backend = self._keyring_api.get_keyring()
            priority = float(getattr(backend, "priority", 0))
        except Exception as exc:
            raise CredentialStoreError(
                "İşletim sistemi parola kasasına erişilemiyor."
            ) from exc
        if priority <= 0:
            raise CredentialStoreError(
                "Güvenli bir işletim sistemi parola kasası bulunamadı."
            )
        return self._keyring_api

    def get(self, profile_id: str) -> str | None:
        api = self._api()
        try:
            return api.get_password(self.SERVICE_NAME, profile_id)
        except Exception as exc:
            raise CredentialStoreError(
                "Sunucu parolası işletim sistemi kasasından okunamadı."
            ) from exc

    def set(self, profile_id: str, password: str) -> None:
        if not password:
            raise ValueError("Sunucu parolası boş olamaz")
        api = self._api()
        try:
            api.set_password(self.SERVICE_NAME, profile_id, password)
            if api.get_password(self.SERVICE_NAME, profile_id) != password:
                raise RuntimeError("credential verification failed")
        except Exception as exc:
            raise CredentialStoreError(
                "Sunucu parolası işletim sistemi kasasına güvenli biçimde kaydedilemedi."
            ) from exc

    def delete(self, profile_id: str) -> None:
        api = self._api()
        try:
            api.delete_password(self.SERVICE_NAME, profile_id)
        except Exception as exc:
            # Missing entries are an idempotent delete; backend failures are
            # surfaced to the extension for safe logging without secret text.
            if type(exc).__name__ not in {"PasswordDeleteError", "ItemNotFoundException"}:
                raise CredentialStoreError(
                    "Kullanılmayan sunucu parolası işletim sistemi kasasından silinemedi."
                ) from exc
