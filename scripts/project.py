#!/usr/bin/env python3
"""Read and check project metadata for the publish action.

Stdlib only, so it runs on a bare runner with nothing installed.

    project.py version         --project-dir DIR
    project.py verify          --project-dir DIR [--module PATH] [--tag TAG]
    project.py python-versions --project-dir DIR --available "3.9 3.10 3.11 ..."
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - environment problem
        raise SystemExit(
            "Reading pyproject.toml needs Python 3.11+ or the 'tomli' package."
        ) from None

VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
BOUND_RE = re.compile(r"(>=|<=|==|!=|~=|<|>)\s*([0-9]+(?:\.[0-9]+)*)")


def fail(message: str) -> None:
    # ::error:: makes GitHub annotate the failing step rather than burying it.
    print(f"::error::{message}", file=sys.stderr)
    raise SystemExit(1)


def load(project_dir: Path) -> dict:
    path = project_dir / "pyproject.toml"
    if not path.is_file():
        fail(f"no pyproject.toml in {project_dir}")
    return tomllib.loads(path.read_text("utf-8"))


def project_version(project_dir: Path) -> str:
    project = load(project_dir).get("project", {})
    if "version" not in project:
        fail(
            f"{project_dir}/pyproject.toml has no static [project] version. "
            f"Dynamic versions are not supported: the tag check and the conda "
            f"recipe both need to read it without building."
        )
    return project["version"]


def cmd_version(args) -> int:
    print(project_version(args.project_dir))
    return 0


def cmd_verify(args) -> int:
    version = project_version(args.project_dir)

    if args.module:
        path = Path(args.module)
        if not path.is_file():
            fail(f"version-module {path} does not exist")
        match = VERSION_RE.search(path.read_text("utf-8"))
        if match is None:
            fail(f"no __version__ assignment found in {path}")
        if match.group(1) != version:
            fail(
                f"version drift: pyproject.toml says {version} but {path} says "
                f"{match.group(1)}"
            )
        print(f"{path} __version__ matches pyproject version {version}")

    if args.tag:
        # A tag only triggers a release; the version comes from pyproject.toml.
        # Publishing a stale version is rejected by PyPI as a duplicate, so fail
        # here rather than after the build.
        if args.tag.removeprefix("v") != version:
            fail(
                f"tag {args.tag} does not match project version {version}; "
                f"bump the version first, or tag v{version}"
            )
        print(f"tag {args.tag} matches project version {version}")

    if not args.module and not args.tag:
        print(f"project version {version}")
    return 0


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def cmd_python_versions(args) -> int:
    """Narrow the interpreters available on the runner to what the project allows."""
    requires = load(args.project_dir).get("project", {}).get("requires-python", "")

    floor: tuple[int, ...] | None = None
    ceiling: tuple[int, ...] | None = None
    ceiling_inclusive = False
    for operator, raw in BOUND_RE.findall(requires):
        bound = parse_version(raw)
        if operator in (">=", "~=", "=="):
            floor = bound if floor is None else max(floor, bound)
        elif operator == ">":
            floor = bound if floor is None else max(floor, bound)
        elif operator == "<":
            ceiling = bound if ceiling is None else min(ceiling, bound)
        elif operator == "<=":
            ceiling = bound if ceiling is None else min(ceiling, bound)
            ceiling_inclusive = True

    selected = []
    for candidate in args.available.split():
        try:
            value = parse_version(candidate)
        except ValueError:
            continue
        if floor is not None and value < floor[: len(value)]:
            continue
        if ceiling is not None:
            if ceiling_inclusive and value > ceiling:
                continue
            if not ceiling_inclusive and value >= ceiling:
                continue
        selected.append(candidate)

    if not selected:
        fail(
            f"no interpreter on this runner satisfies requires-python = "
            f"'{requires}' (available: {args.available or 'none'})"
        )
    print(" ".join(selected))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-dir", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version").set_defaults(func=cmd_version)

    verify = sub.add_parser("verify")
    verify.add_argument("--module", help="File whose __version__ must match")
    verify.add_argument("--tag", help="Tag the version must match, e.g. v1.2.3")
    verify.set_defaults(func=cmd_verify)

    versions = sub.add_parser("python-versions")
    versions.add_argument(
        "--available", default="", help="Space separated X.Y versions offered by uv"
    )
    versions.set_defaults(func=cmd_python_versions)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
