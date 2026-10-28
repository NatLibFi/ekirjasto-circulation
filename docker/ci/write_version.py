"""Generate the same version metadata before building every runtime target."""

import os
import re
import subprocess
from pathlib import Path


def dunamai(*args: str) -> str:
    return subprocess.check_output(["dunamai", "from", "git", *args], text=True).strip()


if __name__ == "__main__":
    source_ref = os.environ.get("BUILD_REF", "")
    branch = dunamai("--format", "{branch}")
    if source_ref.startswith("refs/heads/"):
        branch = source_ref.removeprefix("refs/heads/")
    elif (
        source_ref
        and not source_ref.startswith("refs/")
        and not re.fullmatch(r"[a-fA-F0-9]{40}", source_ref)
    ):
        branch = source_ref
    metadata = {
        "__version__": dunamai("--style", "semver"),
        "__commit__": dunamai("--format", "{commit}", "--full-commit"),
        "__branch__": branch,
    }
    Path("core/_version.py").write_text(
        "".join(f"{key} = {value!r}\n" for key, value in metadata.items()),
        encoding="utf-8",
    )
    print(metadata["__version__"])
