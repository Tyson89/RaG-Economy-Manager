from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

from rag_version import APP_VERSION


PROJECT_ROOT = Path(__file__).resolve().parent
INSTALLER_PATH = PROJECT_ROOT / "dist" / "installer" / "RaG_Economy_Manager_Setup.exe"
CHECKSUM_PATH = Path(f"{INSTALLER_PATH}.sha256")
DIST_APP_DIR = PROJECT_ROOT / "dist" / "RaG_Economy_Manager"

REQUIRED_SOURCE_FILES = (
    "README.md",
    "LICENSE.txt",
    "THIRD_PARTY_NOTICES.txt",
    "rag_version.py",
    "rag_economy_core.py",
    "rag_economy_manager_gui.py",
    "rag_update.py",
    "pbo_core.py",
)
REQUIRED_BUILD_FILES = (
    "assets/HEADONLY_SQUARE_2k.ico",
    "assets/map_icons",
    "installer/RaG_Economy_Manager.iss",
)
BLOCKED_RELEASE_NAMES = {
    ".git",
    ".pytest_cache",
    "TODO.md",
    "RELEASE_NOTES.md",
    "publish_release.ps1",
    "build_rag_economy_manager.ps1",
    "map_assets_manifest.example.json",
    "tests",
    "storage_1",
}


class ReleaseCheckError(RuntimeError):
    pass


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def numeric_version(version: str) -> str:
    parts = re.findall(r"\d+", version)
    while len(parts) < 4:
        parts.append("0")
    return ".".join(parts[:4])


def require_path(path: Path) -> None:
    if not path.exists():
        raise ReleaseCheckError(f"Missing required path: {path.relative_to(PROJECT_ROOT)}")


def check_required_paths(include_build: bool) -> None:
    for relative in REQUIRED_SOURCE_FILES:
        require_path(PROJECT_ROOT / relative)
    if include_build:
        for relative in REQUIRED_BUILD_FILES:
            require_path(PROJECT_ROOT / relative)


def check_version_consistency(include_build: bool) -> None:
    version = APP_VERSION.strip()
    if not version:
        raise ReleaseCheckError("APP_VERSION is empty.")

    readme = read_text(PROJECT_ROOT / "README.md")
    match = re.search(r"Current version:\s*`([^`]+)`", readme)
    if not match:
        raise ReleaseCheckError("README.md has no `Current version` line.")
    if match.group(1).strip() != version:
        raise ReleaseCheckError(f"README.md version is {match.group(1)!r}, expected {version!r}.")

    if not include_build:
        return

    iss = read_text(PROJECT_ROOT / "installer" / "RaG_Economy_Manager.iss")
    app_match = re.search(r'#define\s+AppVersion\s+"([^"]+)"', iss)
    numeric_match = re.search(r'#define\s+AppVersionNumeric\s+"([^"]+)"', iss)
    if not app_match:
        raise ReleaseCheckError("Installer script has no AppVersion define.")
    if app_match.group(1).strip() != version:
        raise ReleaseCheckError(f"Installer AppVersion is {app_match.group(1)!r}, expected {version!r}.")
    expected_numeric = numeric_version(version)
    if not numeric_match:
        raise ReleaseCheckError("Installer script has no AppVersionNumeric define.")
    if numeric_match.group(1).strip() != expected_numeric:
        raise ReleaseCheckError(f"Installer AppVersionNumeric is {numeric_match.group(1)!r}, expected {expected_numeric!r}.")


def check_update_asset_name() -> None:
    from rag_update import INSTALLER_PREFIX, select_installer_asset

    if INSTALLER_PATH.name.casefold().startswith(INSTALLER_PREFIX.casefold()):
        return
    fake_release = {"assets": [{"name": INSTALLER_PATH.name, "browser_download_url": "https://example.invalid/app.exe"}]}
    if not select_installer_asset(fake_release):
        raise ReleaseCheckError(f"Updater will not recognize installer asset: {INSTALLER_PATH.name}")


def check_storage_guard() -> None:
    from rag_economy_core import IGNORED_STORAGE_DIRNAME, is_ignored_storage_path, iter_files_ignoring_storage

    if IGNORED_STORAGE_DIRNAME != "storage_1":
        raise ReleaseCheckError(f"IGNORED_STORAGE_DIRNAME is {IGNORED_STORAGE_DIRNAME!r}, expected 'storage_1'.")
    if not is_ignored_storage_path(Path("mission") / "storage_1" / "players.db"):
        raise ReleaseCheckError("is_ignored_storage_path does not block storage_1.")

    with tempfile.TemporaryDirectory(prefix="rag_release_check_") as temp_name:
        root = Path(temp_name)
        (root / "db").mkdir()
        (root / "db" / "types.xml").write_text("<types />", encoding="utf-8")
        (root / "storage_1").mkdir()
        (root / "storage_1" / "players.db").write_text("do not read", encoding="utf-8")
        found = {path.name for path in iter_files_ignoring_storage(root)}
    if "players.db" in found or "types.xml" not in found:
        raise ReleaseCheckError("iter_files_ignoring_storage did not exclude storage_1 correctly.")


def check_checksum() -> None:
    import hashlib

    require_path(INSTALLER_PATH)
    require_path(CHECKSUM_PATH)
    expected = ""
    for token in read_text(CHECKSUM_PATH).split():
        if re.fullmatch(r"[0-9a-fA-F]{64}", token):
            expected = token.casefold()
            break
    if not expected:
        raise ReleaseCheckError("Installer checksum file has no SHA-256 digest.")

    digest = hashlib.sha256()
    with INSTALLER_PATH.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest().casefold()
    if actual != expected:
        raise ReleaseCheckError(f"Installer checksum mismatch. Expected {expected}, got {actual}.")


def check_dist_contents() -> None:
    require_path(DIST_APP_DIR)
    bad_paths: list[Path] = []
    for root, dirnames, filenames in os.walk(DIST_APP_DIR):
        root_path = Path(root)
        for dirname in dirnames:
            if dirname in BLOCKED_RELEASE_NAMES:
                bad_paths.append(root_path / dirname)
        for filename in filenames:
            if filename in BLOCKED_RELEASE_NAMES or filename.endswith((".spec", ".tmp", ".bak", ".log")):
                bad_paths.append(root_path / filename)
    if bad_paths:
        details = ", ".join(str(path.relative_to(DIST_APP_DIR)) for path in bad_paths[:8])
        raise ReleaseCheckError(f"Blocked file/folder present in packaged app: {details}")


def run_checks(include_build: bool, check_artifacts: bool) -> None:
    check_required_paths(include_build=include_build)
    check_version_consistency(include_build=include_build)
    check_update_asset_name()
    check_storage_guard()
    if check_artifacts:
        check_checksum()
        check_dist_contents()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate RaG Economy Manager release/build readiness.")
    parser.add_argument("--skip-build-files", action="store_true", help="Do not require local installer/assets files.")
    parser.add_argument("--check-artifacts", action="store_true", help="Validate built dist app, installer, and checksum.")
    args = parser.parse_args(argv)

    try:
        run_checks(include_build=not args.skip_build_files, check_artifacts=args.check_artifacts)
    except ReleaseCheckError as exc:
        print(f"Release check failed: {exc}", file=sys.stderr)
        return 1
    print("Release checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
