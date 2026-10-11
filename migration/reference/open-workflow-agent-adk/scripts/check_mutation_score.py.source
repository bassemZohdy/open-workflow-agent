"""Fail CI when the configured mutmut core score drops below 80 percent."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_mutation_score.py MUTMUT_LOG")
    text = Path(sys.argv[1]).read_text()
    matches = re.findall(r"(\d+)/(\d+)\s+🎉\s+(\d+).*?🙁\s+(\d+)", text)
    if not matches:
        raise SystemExit("could not find a completed mutmut summary")
    _, total, killed, survived = map(int, matches[-1])
    tested = killed + survived
    score = killed / tested if tested else 0
    print(f"mutation score: {killed}/{tested} = {score:.1%} (generated {total})")
    if score < 0.8:
        raise SystemExit("mutation score is below the required 80%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
