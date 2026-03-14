import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
import config


GITHUB_API = "https://api.github.com"


def request_json(url: str, token: str | None = None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "revanced-builder"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


def request_text(url: str, token: str | None = None) -> str:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "revanced-builder"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.text


def latest_release(repo: str, token: str | None = None) -> dict[str, Any]:
    return request_json(f"{GITHUB_API}/repos/{repo}/releases/latest", token)


def find_asset(assets: list[dict[str, Any]], patterns: list[str]) -> dict[str, Any]:
    for pattern in patterns:
        regex = re.compile(pattern)
        for asset in assets:
            if regex.search(asset.get("name", "")):
                return asset
    raise RuntimeError(f"No asset matched {patterns}")


def find_asset_or_none(assets: list[dict[str, Any]], patterns: list[str]) -> dict[str, Any] | None:
    try:
        return find_asset(assets, patterns)
    except RuntimeError:
        return None


def download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=180) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def resolve_supported_youtube_version_from_cli(cli_jar: Path, patches_bundle: Path) -> str:
    command = [
        "java",
        "-jar",
        str(cli_jar),
        "list-versions",
        "-f",
        config.YOUTUBE_PACKAGE,
        str(patches_bundle),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    output = result.stdout + "\n" + result.stderr
    versions = re.findall(r"\b(\d+\.\d+\.\d+)\s+\(\d+\s+patches\)", output)
    if versions:
        return versions[0]
    fallback = re.findall(r"\b(\d+\.\d+\.\d+)\b", output)
    if fallback:
        return fallback[0]
    raise RuntimeError("Could not resolve a compatible YouTube version from ReVanced CLI")


def parse_html(url: str) -> BeautifulSoup:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    }
    response = requests.get(url, headers=headers, timeout=90)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def full_apkmirror_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return f"https://www.apkmirror.com{href}"


def resolve_youtube_download_url(version: str) -> str:
    slug = version.replace(".", "-")
    release_page = parse_html(
        f"https://www.apkmirror.com/apk/google-inc/youtube/youtube-{slug}-release/"
    )
    variant_url = None
    for anchor in release_page.select("a"):
        href = anchor.get("href", "")
        text = (anchor.get_text() or "").lower()
        if "/apk/google-inc/youtube/youtube-" in href and ("nodpi" in text or "variant" in href):
            variant_url = full_apkmirror_url(href)
            break
    if not variant_url:
        raise RuntimeError(f"Variant page not found for YouTube {version}")

    variant_page = parse_html(variant_url)
    download_page_url = None
    for anchor in variant_page.select("a"):
        href = anchor.get("href", "")
        text = (anchor.get_text() or "").lower()
        if "/download/" in href and "download" in text:
            download_page_url = full_apkmirror_url(href)
            break
    if not download_page_url:
        raise RuntimeError(f"Download page not found for YouTube {version}")

    download_page = parse_html(download_page_url)
    button = download_page.select_one("a#downloadButton")
    if button and button.get("href"):
        return full_apkmirror_url(button.get("href", ""))

    for anchor in download_page.select("a"):
        href = anchor.get("href", "")
        if "/wp-content/themes/APKMirror/download.php" in href:
            return full_apkmirror_url(href)
    raise RuntimeError(f"Final APK url not found for YouTube {version}")


def run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_output(key: str, value: str) -> None:
    github_output = Path(".github_output.txt")
    output_value = os.getenv("GITHUB_OUTPUT")
    if output_value:
        github_output = Path(output_value)
    with github_output.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def latest_existing_manifest(repo: str, token: str | None = None) -> dict[str, Any] | None:
    try:
        release = latest_release(repo, token)
    except Exception:
        return None
    assets = release.get("assets", [])
    update_asset = next((asset for asset in assets if asset.get("name") == "update.json"), None)
    if not update_asset:
        return None
    text = request_text(update_asset["browser_download_url"], token=None)
    return json.loads(text)


def build() -> None:
    token = os.getenv("GITHUB_TOKEN")
    workspace = Path.cwd()
    work = workspace / "work"
    dist = workspace / "dist"
    work.mkdir(exist_ok=True, parents=True)
    dist.mkdir(exist_ok=True, parents=True)

    cli_release = latest_release(config.REVANCED_CLI_REPO, token)
    patches_release = latest_release(config.REVANCED_PATCHES_REPO, token)
    integrations_release = latest_release(config.REVANCED_INTEGRATIONS_REPO, token)

    current_manifest = latest_existing_manifest(config.GITHUB_REPOSITORY, token)
    current_patches_tag = (
        ((current_manifest or {}).get("revanced") or {}).get("patches_tag") if current_manifest else None
    )
    if current_patches_tag == patches_release["tag_name"]:
        write_output("built", "false")
        write_output("reason", "latest_patches_already_built")
        write_output("patches_tag", patches_release["tag_name"])
        return

    cli_asset = find_asset(cli_release.get("assets", []), [r"revanced-cli-.*\.jar$", r"\.jar$"])
    patches_asset = find_asset(
        patches_release.get("assets", []), [r"revanced-patches-.*\.rvp$", r"\.rvp$", r"\.jar$"]
    )
    integrations_asset = find_asset(integrations_release.get("assets", []), [r"\.apk$"])

    cli_jar = work / cli_asset["name"]
    patches_bundle = work / patches_asset["name"]
    integrations_apk = work / integrations_asset["name"]
    youtube_apk = work / "youtube.apk"
    patched_apk = dist / "youtube-revanced.apk"

    download_file(cli_asset["browser_download_url"], cli_jar)
    download_file(patches_asset["browser_download_url"], patches_bundle)
    download_file(integrations_asset["browser_download_url"], integrations_apk)

    yt_version = resolve_supported_youtube_version_from_cli(cli_jar, patches_bundle)
    
    # Check for local APK or download link
    local_apk_path = Path("youtube.apk")
    alt_local_apk = Path("youtube_20.14.43.apk")
    
    if alt_local_apk.exists():
        print(f"Using local {alt_local_apk}", file=sys.stderr)
        if alt_local_apk.resolve() != youtube_apk.resolve():
            import shutil
            shutil.copy(alt_local_apk, youtube_apk)
    elif local_apk_path.exists():
        print(f"Using local youtube.apk", file=sys.stderr)
        # Verify version if possible, or just assume user knows what they are doing
        # Or better, we rename it to work/youtube.apk
        if local_apk_path.resolve() != youtube_apk.resolve():
            import shutil
            shutil.copy(local_apk_path, youtube_apk)
    elif os.getenv("YOUTUBE_APK_URL"):
        print(f"Downloading YouTube APK from provided URL", file=sys.stderr)
        download_file(os.getenv("YOUTUBE_APK_URL"), youtube_apk)
    else:
        print(f"Attempting to download YouTube {yt_version} from APKMirror", file=sys.stderr)
        try:
            yt_url = resolve_youtube_download_url(yt_version)
            download_file(yt_url, youtube_apk)
        except Exception as e:
            print(f"Failed to download from APKMirror: {e}", file=sys.stderr)
            print(f"Please manually download YouTube {yt_version} (nodpi) and place it as 'youtube.apk' in the root directory.", file=sys.stderr)
            print(f"Or provide a direct download link via YOUTUBE_APK_URL environment variable.", file=sys.stderr)
            sys.exit(1)

    keystore = workspace / config.KEYSTORE_FILE
    
    # Exact patches requested by user
    selected_patches = [
        "Playback speed",
        "Video quality",
        "GmsCore support",
        "Hide ads",
        "Video ads",
        "Downloads",
        "Disable resuming Shorts on startup",
        "Remove background playback restrictions",
        "Check watch history domain name resolution"
    ]
    
    command = [
        "java",
        "-jar",
        str(cli_jar),
        "patch",
        "--patch-bundle",
        str(patches_bundle),
        "--merge",
        str(integrations_apk),
        "--out",
        str(patched_apk),
        "--keystore",
        str(keystore),
        "--keystore-password",
        config.KEYSTORE_PASSWORD,
        "--keystore-entry-alias",
        config.KEY_ALIAS,
        "--keystore-entry-password",
        config.KEY_ALIAS_PASSWORD,
        "--exclusive"  # Only include explicitly specified patches
    ]
    
    for patch in selected_patches:
        command.extend(["--include", patch])
        
    command.append(str(youtube_apk))
    
    run_command(command)

    build_tag = f"rv-{patches_release['tag_name'].replace('.', '-')}-{yt_version.replace('.', '-')}"
    artifact_sha256 = sha256sum(patched_apk)
    manifest = {
        "app_id": config.YOUTUBE_PACKAGE,
        "build_tag": build_tag,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "source": {"youtube_version": yt_version, "youtube_download_url": yt_url},
        "revanced": {
            "cli_tag": cli_release["tag_name"],
            "patches_tag": patches_release["tag_name"],
            "integrations_tag": integrations_release["tag_name"],
        },
        "artifact": {
            "name": patched_apk.name,
            "sha256": artifact_sha256,
            "download_url": f"https://github.com/{config.GITHUB_REPOSITORY}/releases/download/{build_tag}/{patched_apk.name}",
        },
    }
    manifest_path = dist / "update.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    write_output("built", "true")
    write_output("build_tag", build_tag)
    write_output("youtube_version", yt_version)
    write_output("patches_tag", patches_release["tag_name"])
    write_output("cli_tag", cli_release["tag_name"])
    write_output("integrations_tag", integrations_release["tag_name"])
    write_output("artifact_sha256", artifact_sha256)


if __name__ == "__main__":
    try:
        build()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
