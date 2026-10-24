"""Fixed, secret-safe Connector errors."""


class CredentialStoreUnavailable(RuntimeError):
    """A key, ciphertext, or credential payload could not be handled safely."""

    code = "credential_store_unavailable"

    def __init__(self) -> None:
        super().__init__(self.code)
