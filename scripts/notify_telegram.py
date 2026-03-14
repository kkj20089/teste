import json
import sys
from pathlib import Path

import requests

import config


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def send_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": False}
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data)


def build_message(manifest: dict) -> str:
    revanced = manifest.get("revanced", {})
    source = manifest.get("source", {})
    artifact = manifest.get("artifact", {})
    return (
        f"New ReVanced YouTube build is available\n"
        f"Build tag: {manifest.get('build_tag')}\n"
        f"YouTube version: {source.get('youtube_version')}\n"
        f"Patches: {revanced.get('patches_tag')}\n"
        f"CLI: {revanced.get('cli_tag')}\n"
        f"Integrations: {revanced.get('integrations_tag')}\n"
        f"Download: {artifact.get('download_url')}"
    )


def main() -> None:
    if config.TELEGRAM_BOT_TOKEN.startswith("CHANGE_ME_"):
        return
    if config.TELEGRAM_CHAT_ID.startswith("CHANGE_ME_"):
        return
    manifest_path = Path("dist/update.json")
    if not manifest_path.exists():
        raise RuntimeError("dist/update.json not found")
    manifest = load_manifest(manifest_path)
    send_message(build_message(manifest))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
