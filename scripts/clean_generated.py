from __future__ import annotations

import argparse
import shutil
from pathlib import Path


GENERATED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

PROTECTED_DIR_NAMES = {
    ".git",
    ".idea",
    ".playwright",
    ".venv",
    "venv",
    "env",
    "ENV",
    "configs",
    "docs",
    "examples",
    "legacy",
    "output",
}

GENERATED_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
}


def is_generated_temp_file(path: Path) -> bool:
    name = path.name
    return (
        path.suffix in GENERATED_FILE_SUFFIXES
        or name.endswith(".tmp")
        or (name.startswith(".") and name.endswith(".tmp"))
    )


def iter_generated_paths(root: Path) -> list[Path]:
    root = root.resolve()
    generated: list[Path] = []

    def walk(directory: Path) -> None:
        for child in directory.iterdir():
            if child.is_dir():
                if child.name in GENERATED_DIR_NAMES:
                    generated.append(child)
                    continue
                if child.name in PROTECTED_DIR_NAMES:
                    continue
                walk(child)
            elif child.is_file() and is_generated_temp_file(child):
                generated.append(child)

    walk(root)
    return sorted(generated, key=lambda path: (len(path.parts), str(path)))


def clean_generated(root: Path, dry_run: bool = True) -> list[Path]:
    root = root.resolve()
    generated_paths = iter_generated_paths(root)

    if dry_run:
        return generated_paths

    for path in sorted(generated_paths, key=lambda item: len(item.parts), reverse=True):
        resolved = path.resolve()
        if root not in resolved.parents and resolved != root:
            raise RuntimeError(f"Refusing to clean outside project root: {resolved}")
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    return generated_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove generated Python/test cache files without touching runtime outputs."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root to clean. Defaults to this repository.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show paths that would be removed without deleting them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    dry_run = args.dry_run
    paths = clean_generated(root, dry_run=dry_run)

    action = "Would remove" if dry_run else "Removed"
    if not paths:
        print("No generated cache files found.")
        return 0

    print(f"{action} {len(paths)} generated path(s):")
    for path in paths:
        print(path.relative_to(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
