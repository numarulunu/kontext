"""Workspace manifest validation for cross-device parity."""


def validate_manifest(local: dict[str, str], remote: dict[str, str]) -> None:
    """Reject replay when behavior-critical manifest values diverge."""
    keys = (
        "schema_version",
        "embedding_model",
        "ranking_version",
        "prompt_routing_version",
    )
    mismatches = [key for key in keys if str(local.get(key)) != str(remote.get(key))]
    if mismatches:
        raise ValueError(f"manifest mismatch: {', '.join(mismatches)}")
