from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


GITHUB_REPOSITORY = "Tyson89/RaG-Economy-Manager"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases?per_page=20"
INSTALLER_PREFIX = "rag_economy_manager_setup"


class UpdateError(RuntimeError):
    pass


def version_tuple(value):
    parts = tuple(int(part) for part in re.findall(r"\d+", str(value)))
    return parts or (0,)


def is_newer_version(candidate, current):
    candidate_parts = version_tuple(candidate)
    current_parts = version_tuple(current)
    width = max(len(candidate_parts), len(current_parts))
    return candidate_parts + (0,) * (width - len(candidate_parts)) > current_parts + (0,) * (width - len(current_parts))


def select_installer_asset(release):
    assets = release.get("assets", []) if isinstance(release, dict) else []
    installers = [asset for asset in assets if str(asset.get("name", "")).casefold().endswith(".exe")]
    for asset in installers:
        name = str(asset.get("name", "")).casefold()
        if name.startswith(INSTALLER_PREFIX):
            return asset
    for asset in installers:
        if "setup" in str(asset.get("name", "")).casefold():
            return asset
    return None


def select_checksum_asset(release, installer_name):
    assets = release.get("assets", []) if isinstance(release, dict) else []
    installer_name = str(installer_name)
    expected_names = {
        f"{installer_name}.sha256".casefold(),
        f"{Path(installer_name).stem}.sha256".casefold(),
        "sha256sums.txt",
        "checksums.txt",
    }
    for asset in assets:
        if str(asset.get("name", "")).casefold() in expected_names:
            return asset
    return None


def select_latest_update(releases, current_version):
    candidates = []
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        version = str(release.get("tag_name") or release.get("name") or "").strip()
        installer = select_installer_asset(release)
        if version and installer and is_newer_version(version, current_version):
            candidates.append((version_tuple(version), version, release, installer))
    if not candidates:
        return None
    _parts, version, release, installer = max(candidates, key=lambda item: item[0])
    return {
        "version": version,
        "name": str(release.get("name") or version),
        "notes": str(release.get("body") or "").strip(),
        "release_url": str(release.get("html_url") or ""),
        "installer": installer,
        "checksum": select_checksum_asset(release, installer.get("name", "")),
    }


def github_request(url, accept="application/vnd.github+json", timeout=30):
    headers = {
        "Accept": accept,
        "User-Agent": "RaG-Economy-Manager-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Update repository is not publicly accessible.") from exc
        raise UpdateError(f"GitHub request failed: HTTP {exc.code} {exc.reason}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"GitHub request failed: {exc}") from exc


def check_for_update(current_version):
    try:
        releases = json.loads(github_request(GITHUB_RELEASES_API).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned invalid release data.") from exc
    if not isinstance(releases, list):
        raise UpdateError("GitHub returned an unexpected release response.")
    return select_latest_update(releases, current_version)


def parse_checksum(text, installer_name):
    fallback = ""
    for line in str(text).splitlines():
        match = re.search(r"\b([0-9a-fA-F]{64})\b(?:\s+[* ]?(.+))?", line.strip())
        if not match:
            continue
        digest = match.group(1).casefold()
        filename = (match.group(2) or "").strip()
        if filename and Path(filename).name.casefold() == Path(installer_name).name.casefold():
            return digest
        if not fallback:
            fallback = digest
    return fallback


def expected_installer_digest(update):
    installer = update["installer"]
    digest = str(installer.get("digest") or "")
    if digest.casefold().startswith("sha256:"):
        value = digest.split(":", 1)[1].strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    checksum_asset = update.get("checksum")
    if checksum_asset:
        checksum_url = str(checksum_asset.get("browser_download_url") or "")
        if checksum_url:
            checksum_text = github_request(checksum_url, accept="application/octet-stream", timeout=30).decode("utf-8", errors="replace")
            return parse_checksum(checksum_text, installer.get("name", ""))
    return ""


def download_update(update, output_dir=None):
    installer = update["installer"]
    installer_name = Path(str(installer.get("name") or "RaG_Economy_Manager_Setup.exe")).name
    download_url = str(installer.get("browser_download_url") or "")
    if not download_url:
        raise UpdateError("Release installer has no download URL.")
    expected_digest = expected_installer_digest(update)
    if not expected_digest:
        raise UpdateError("Release installer has no SHA-256 digest or checksum asset.")

    target_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "RaG Economy Manager Updates"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / installer_name
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(download_url, headers={"User-Agent": "RaG-Economy-Manager-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
    except (OSError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Installer download failed: {exc}") from exc

    actual_digest = digest.hexdigest().casefold()
    if actual_digest != expected_digest.casefold():
        partial.unlink(missing_ok=True)
        raise UpdateError("Downloaded installer failed SHA-256 verification.")
    os.replace(partial, target)
    return target
