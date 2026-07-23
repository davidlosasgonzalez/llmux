"""Credential presence checks over the provider catalog.

Split out of :mod:`provider_catalog` (rather than added there) to avoid a
circular import: this module needs the concrete ``Settings`` type, and
``settings.py`` itself imports ``SUPPORTED_PROVIDER_IDS`` from
``provider_catalog``.
"""

from .provider_catalog import PROVIDER_CATALOG
from .settings import Settings


def provider_has_credential(provider: str, settings: Settings) -> bool:
    """True when ``settings`` carries a usable credential/endpoint for provider."""
    descriptor = PROVIDER_CATALOG.get(provider)
    if descriptor is None:
        return False
    if descriptor.static_credential:
        return True
    if descriptor.credential_attr:
        value = getattr(settings, descriptor.credential_attr, "")
        if isinstance(value, str) and value.strip():
            return True
    if descriptor.local and descriptor.base_url_attr:
        value = getattr(settings, descriptor.base_url_attr, "")
        return bool(isinstance(value, str) and value.strip())
    return False
