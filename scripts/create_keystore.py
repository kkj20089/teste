import subprocess
import sys
from pathlib import Path

import config


def main() -> None:
    target = Path(config.KEYSTORE_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    command = [
        "keytool",
        "-genkeypair",
        "-v",
        "-keystore",
        str(target),
        "-alias",
        config.KEY_ALIAS,
        "-keyalg",
        "RSA",
        "-keysize",
        "4096",
        "-validity",
        "36500",
        "-storepass",
        config.KEYSTORE_PASSWORD,
        "-keypass",
        config.KEY_ALIAS_PASSWORD,
        "-dname",
        "CN=ReVanced, OU=Personal, O=Personal, L=NA, ST=NA, C=US",
    ]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
