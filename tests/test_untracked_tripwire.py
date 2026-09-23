"""Tripwire: too many untracked files in the repo.

The Codex desktop app (ChatGPT.exe) walks untracked files and spawns one
`git diff --no-index` per file. Every spawn is scanned by Defender. Enough
untracked files and the machine stops responding - twice now (2026-09-09
with 43k files, 2026-09-23 with 2,464). This test refuses to let that
recur silently: if the untracked count in this repo climbs above a small
threshold, the keep-set fails BEFORE a push and prints, in plain English,
which folder is the offender and the exact line to add to .gitignore to
make it invisible to that walker.

Runs in every keep-set. No git vocabulary in the failure message.
Card: t_40e532e2.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

# Above this, the ChatGPT.exe git-diff walk starts to hurt. 200 is a low
# ceiling: a healthy repo sits well under this.
THRESHOLD = 200

REPO_ROOT = Path(__file__).resolve().parent.parent


def _untracked_files() -> list[str]:
    """Return every untracked path git would show to a walker."""
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _top_offender(paths: list[str]) -> tuple[str, int]:
    """Bucket by top-level directory (or filename for root files)."""
    buckets: Counter[str] = Counter()
    for p in paths:
        head = p.split("/", 1)[0]
        buckets[head] += 1
    top, count = buckets.most_common(1)[0]
    return top, count


def test_untracked_file_count_under_threshold() -> None:
    paths = _untracked_files()
    total = len(paths)
    if total <= THRESHOLD:
        return

    top, top_count = _top_offender(paths)
    # If the top offender is a directory, the gitignore line ends with a slash.
    # If it's a lone file at the repo root, the line is just the filename.
    top_path = REPO_ROOT / top
    is_dir = top_path.is_dir()
    ignore_line = f"{top}/" if is_dir else top

    msg = (
        "\n"
        "\n"
        f"There are {total} files sitting in this repo that git is not\n"
        f"tracking. That is too many. When Harry uses an app that peeks\n"
        f"at every file git does not know about (the Codex desktop app,\n"
        f"and ChatGPT.exe does this), each file starts its own Windows\n"
        f"Defender scan and the machine slows to a crawl.\n"
        f"\n"
        f"The biggest chunk is here:\n"
        f"    {top}    ({top_count} files)\n"
        f"\n"
        f"To hide it from that walker, add this one line to the file\n"
        f"named .gitignore at the top of this repo, on its own line:\n"
        f"\n"
        f"    {ignore_line}\n"
        f"\n"
        f"Then re-run the keep-set. Do not commit or delete anything;\n"
        f"just add that one line. Threshold is {THRESHOLD}.\n"
    )
    raise AssertionError(msg)
