"""Local on-disk store for workspace/device keys and bearer tokens."""
import os
import stat
from pathlib import Path

from nacl.public import PrivateKey


DEFAULT_KEYSTORE_ROOT = Path.home() / ".kontext" / "keys"


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value).strip("-") or "workspace"


def _set_private_mode(path: Path) -> None:
    if os.name == "posix":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def workspace_dir(workspace_id: str, root: Path | None = None) -> Path:
    base = root or DEFAULT_KEYSTORE_ROOT
    target = base / _safe_id(workspace_id)
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(target, stat.S_IRWXU)
    return target


def save_device_private_key(workspace_id: str, device_id: str, private_key_bytes: bytes,
                            root: Path | None = None) -> Path:
    path = workspace_dir(workspace_id, root) / f"{_safe_id(device_id)}.key"
    path.write_bytes(private_key_bytes)
    _set_private_mode(path)
    return path


def load_device_private_key(workspace_id: str, device_id: str,
                            root: Path | None = None) -> bytes | None:
    path = workspace_dir(workspace_id, root) / f"{_safe_id(device_id)}.key"
    if not path.exists():
        return None
    return path.read_bytes()


def generate_device_keypair() -> tuple[bytes, bytes]:
    private = PrivateKey.generate()
    return bytes(private), bytes(private.public_key)


def save_workspace_token(workspace_id: str, token: str, root: Path | None = None) -> Path:
    path = workspace_dir(workspace_id, root) / "workspace.token"
    path.write_text(token, encoding="utf-8")
    _set_private_mode(path)
    return path


def load_workspace_token(workspace_id: str, root: Path | None = None) -> str | None:
    path = workspace_dir(workspace_id, root) / "workspace.token"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()
