#!/usr/bin/env python3
"""
Interactively remove failed scrapes from a profiles list.

Typical usage:
  python remove_failed_interactive.py \
    --progress output/progress.json \
    --profiles profiles_to_scrape.txt

This will:
  - load the "failed" list from the progress JSON
  - match entries to lines in profiles_to_scrape.txt (by normalized key/slug)
  - prompt you for each matching line before removing
  - write the updated profiles file once, creating a timestamped backup first
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse


SLUG_RE = re.compile(r"linkedin\.com/(?:in|pub)/([^/?#]+)", flags=re.IGNORECASE)
PATH_SLUG_RE = re.compile(r"^(?:in|pub)/([^/?#]+)$", flags=re.IGNORECASE)


def _normalize_key(s: str) -> str:
    # Keep this intentionally conservative: lower + trim + strip trailing slashes.
    return s.strip().strip("/").lower()


def extract_profile_key(raw_line_or_id: str) -> Optional[str]:
    """
    Convert a profiles_to_scrape line (or a failed id) into a comparable key.

    Supported inputs:
      - https://(www.)linkedin.com/in/<slug>[/]
      - linkedin.com/in/<slug>
      - <slug>
      - other strings (treated as-is after normalization), e.g. "vidysea.com"
    """
    s = raw_line_or_id.strip()
    if not s or s.startswith("#"):
        return None

    # Try explicit linkedin URL patterns anywhere in the string first.
    m = SLUG_RE.search(s)
    if m:
        return _normalize_key(m.group(1))

    # If it looks like a URL, parse it and try to interpret path.
    if "://" in s:
        try:
            parsed = urlparse(s)
        except Exception:
            return _normalize_key(s)
        path = (parsed.path or "").strip("/")
        m2 = PATH_SLUG_RE.match(path)
        if m2:
            return _normalize_key(m2.group(1))
        return _normalize_key(s)

    # Fallback: treat as a raw slug/id.
    return _normalize_key(s)


def stable_dedupe(seq: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in seq:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


@dataclass(frozen=True)
class Match:
    key: str
    failed_id: str
    line_no: int  # 1-based
    line_text: str


def load_failed_ids(progress_path: Path) -> List[str]:
    data = json.loads(progress_path.read_text(encoding="utf-8", errors="replace"))
    failed = data.get("failed")
    if not isinstance(failed, list):
        raise ValueError(f'Expected "failed" to be a list in {progress_path}')
    # Keep order but remove duplicates.
    failed_strs = [str(x) for x in failed]
    return stable_dedupe([x for x in failed_strs if x.strip()])


def index_profiles_lines(lines: Sequence[str]) -> Dict[str, List[int]]:
    key_to_indices: Dict[str, List[int]] = {}
    for idx, line in enumerate(lines):
        key = extract_profile_key(line)
        if not key:
            continue
        key_to_indices.setdefault(key, []).append(idx)
    return key_to_indices


def make_backup_path(profiles_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = profiles_path.with_name(profiles_path.name + f".bak.{ts}")
    # Avoid collisions in the unlikely event of same-second runs.
    if not candidate.exists():
        return candidate
    for n in range(1, 1000):
        p = profiles_path.with_name(profiles_path.name + f".bak.{ts}.{n}")
        if not p.exists():
            return p
    raise RuntimeError("Could not find a free backup filename")


def prompt_yes_no(prompt: str) -> bool:
    ans = input(prompt).strip().lower()
    return ans in {"y", "yes"}


def prompt_action(prompt: str) -> str:
    """
    Returns one of: 'y', 'n', 'q'
    """
    ans = input(prompt).strip().lower()
    if ans in {"q", "quit"}:
        return "q"
    if ans in {"y", "yes"}:
        return "y"
    return "n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactively remove failed scrapes from profiles_to_scrape.txt"
    )
    parser.add_argument(
        "--progress",
        type=Path,
        default=Path("output/progress_1.json"),
        help="Path to progress JSON containing a 'failed' list",
    )
    parser.add_argument(
        "--profiles",
        type=Path,
        default=Path("profiles_to_scrape.txt"),
        help="Path to profiles list (one URL or username per line)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write changes; just show prompts and counts",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Remove all matches without prompting",
    )
    parser.add_argument(
        "--quit-after-first",
        action="store_true",
        help="Exit after the first confirmed removal (useful for very manual workflows)",
    )

    args = parser.parse_args(argv)

    progress_path: Path = args.progress
    profiles_path: Path = args.profiles

    failed_ids = load_failed_ids(progress_path)
    lines = profiles_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    key_to_indices = index_profiles_lines(lines)

    removed_indices: Set[int] = set()
    matches_considered = 0
    matches_found = 0

    for fi, failed_id in enumerate(failed_ids, start=1):
        key = extract_profile_key(failed_id)
        if not key:
            continue

        indices = key_to_indices.get(key, [])
        if not indices:
            continue

        for idx in indices:
            if idx in removed_indices:
                continue

            matches_found += 1
            matches_considered += 1
            line_no = idx + 1
            line_text = lines[idx]

            header = f"[{fi}/{len(failed_ids)}] failed: {failed_id}  (key: {key})"
            print(header)
            print(f"  match line {line_no}: {line_text}")

            if args.yes:
                do_remove = True
            else:
                action = prompt_action("Remove this line? [y/N/q] ")
                if action == "q":
                    print("Quitting early.")
                    fi = len(failed_ids)  # for clearer summary semantics
                    break
                do_remove = action == "y"

            if do_remove:
                removed_indices.add(idx)
                print("  removed.")
                if args.quit_after_first:
                    print("Exiting after first removal as requested.")
                    break
            else:
                print("  kept.")

        else:
            continue  # inner loop completed normally
        break  # inner loop broke (quit/quit-after-first)

    if not removed_indices:
        print("No lines selected for removal. No changes made.")
        return 0

    new_lines = [ln for i, ln in enumerate(lines) if i not in removed_indices]

    print("")
    print(f"Summary:")
    print(f"  failed IDs (deduped): {len(failed_ids)}")
    print(f"  matches found in profiles file: {matches_found}")
    print(f"  lines removed: {len(removed_indices)}")
    print(f"  profiles lines: {len(lines)} -> {len(new_lines)}")

    if args.dry_run:
        print("Dry run enabled; not writing files.")
        return 0

    backup_path = make_backup_path(profiles_path)
    backup_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    profiles_path.write_text(
        "\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8"
    )
    print("")
    print(f"Wrote updated profiles file: {profiles_path}")
    print(f"Backup saved as: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

