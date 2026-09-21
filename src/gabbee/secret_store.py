from __future__ import annotations

import os


SERVICE_NAME = "Gabbee"
ACCOUNT = "elevenlabs-api-key"
ATTRIBUTES = {"application": "gabbee", "provider": "elevenlabs", "kind": "api-key"}


def _collection():
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return None
    try:
        import secretstorage

        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            unlocked, _prompted = collection.unlock()
            if not unlocked and collection.is_locked():
                return None
        return collection
    except Exception:
        return None


def load_api_key() -> str | None:
    """Read a new-style key from Secret Service without touching legacy .env."""

    collection = _collection()
    if collection is None:
        return None
    try:
        items = collection.search_items(ATTRIBUTES)
        for item in items:
            if item.is_locked():
                continue
            secret = item.get_secret().decode("utf-8").strip()
            if secret:
                return secret
    except Exception:
        return None
    return None


def save_api_key(api_key: str) -> bool:
    """Save a key to KDE Wallet/Secret Service, replacing Gabbee's prior item."""

    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API key cannot be empty.")
    collection = _collection()
    if collection is None:
        return False
    try:
        collection.create_item(
            f"{SERVICE_NAME}: {ACCOUNT}",
            ATTRIBUTES,
            api_key.encode("utf-8"),
            replace=True,
        )
    except Exception:
        return False
    return True
