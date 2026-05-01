import subprocess
import os
import logging
import re
import pathlib

logger = logging.getLogger(__name__)

# Allowlist: only these extra rclone flags may be passed in
_ALLOWED_EXTRA_FLAGS: set[str] = {
    "--dry-run",
    "--verbose",
    "--no-traverse",
}

# rclone remote name must be simple alphanumeric (no shell metacharacters)
_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _validate_remote(remote_and_path: str) -> None:
    """
    Ensure the remote name contains only safe characters.
    Format expected: <remote_name>:<path>
    """
    if ":" not in remote_and_path:
        raise ValueError(f"Invalid remote format (missing ':'): {remote_and_path!r}")
    remote_name = remote_and_path.split(":", 1)[0]
    if not _REMOTE_NAME_RE.match(remote_name):
        raise ValueError(
            f"Remote name contains unsafe characters: {remote_name!r}"
        )


def rclone_list_remotes(rclone_conf_path: str) -> list[str]:
    """Parse remote names from an rclone config file."""
    conf = pathlib.Path(rclone_conf_path)
    if not conf.exists():
        return []

    remotes: list[str] = []
    with conf.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                name = line[1:-1]
                # Only expose valid remote names
                if _REMOTE_NAME_RE.match(name):
                    remotes.append(name)
    return remotes


def rclone_copy(
    local_path: str,
    remote_and_path: str,
    rclone_conf_path: str,
    extra_args: list[str] | None = None,
) -> str:
    """
    Run `rclone copy` safely.

    Security hardening:
    - extra_args is validated against an allowlist (no arbitrary flag injection)
    - remote name is validated against a strict regex
    - subprocess never uses shell=True
    """
    if extra_args is None:
        extra_args = []

    # ── Validate inputs ────────────────────────────────────────────────────────
    _validate_remote(remote_and_path)

    safe_extra: list[str] = []
    for arg in extra_args:
        if arg not in _ALLOWED_EXTRA_FLAGS:
            logger.warning("Rejected unsafe rclone flag: %r", arg)
        else:
            safe_extra.append(arg)

    rclone_bin = "/usr/bin/rclone"
    if not pathlib.Path(rclone_bin).is_file():
        raise FileNotFoundError(f"rclone binary not found at {rclone_bin}")

    cmd: list[str] = [
        rclone_bin, "copy",
        "--progress",
        local_path,
        remote_and_path,
        "--config", rclone_conf_path,
        "--transfers", "4",
        "--checkers", "8",
        "--drive-chunk-size", "32M",
    ] + safe_extra

    logger.info("Running rclone: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,          # Never use shell=True with user-influenced data
    )

    if result.returncode != 0:
        logger.error("rclone error:\n%s", result.stderr)
        raise RuntimeError(f"rclone failed: {result.stderr or result.stdout}")

    return result.stdout
