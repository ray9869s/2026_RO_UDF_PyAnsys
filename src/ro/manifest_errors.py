"""Shared manifest exception types."""


class ManifestError(ValueError):
    """Raised when a manifest is invalid, stale, or unsafe to overwrite."""
