#!/usr/bin/env python3
"""Render a rattler-build recipe (recipe.yaml, format v1) from pyproject.toml.

pyproject.toml stays the single source of truth for version, dependencies and
metadata; this translates it into a conda recipe so the conda package can never
drift from the PyPI one. Conda-only settings live in [tool.conda-recipe].

The recipe is built from the project's *sdist*, extracted next to recipe.yaml,
so the conda package is built from exactly the bytes that go to PyPI and the
recipe directory is self-contained.

    python conda_recipe.py --project-dir . --sdist dist/pkg-1.2.3.tar.gz

Stdlib only, so it runs on a bare runner with no environment to set up.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tarfile
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


# --------------------------------------------------------------------------
# PEP 508 -> conda dependency translation
# --------------------------------------------------------------------------

REQUIREMENT_RE = re.compile(
    r"""^\s*
    (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)          # project name
    (?:\s*\[(?P<extras>[^\]]*)\])?                # optional extras
    (?P<specifier>[^;@]*)                         # version specifier set
    (?:;\s*(?P<marker>.*))?                       # environment marker
    \s*$""",
    re.VERBOSE,
)
CLAUSE_RE = re.compile(r"^(===|==|!=|<=|>=|~=|<|>)\s*(\S+)$")


class RequirementError(ValueError):
    """A requirement that cannot be expressed in conda's syntax."""


def normalize(name: str) -> str:
    """Normalize a project name the way PEP 503 does."""
    return re.sub(r"[-_.]+", "-", name).lower()


def compatible_release(version: str) -> str:
    """Expand a PEP 440 ``~=`` clause into conda's two-sided form."""
    parts = version.split(".")
    if len(parts) < 2:
        raise RequirementError(f"'~={version}' needs at least two version parts")
    upper = parts[:-1]
    try:
        upper[-1] = str(int(upper[-1]) + 1)
    except ValueError:
        raise RequirementError(f"cannot compute an upper bound for '~={version}'") from None
    upper.extend("0" for _ in parts[len(upper) :])
    # The a0 suffix keeps prereleases of the next version out, as conda-forge does.
    return f">={version},<{'.'.join(upper)}a0"


def convert_specifier(specifier: str) -> str:
    """Translate a PEP 440 specifier set into a conda version constraint."""
    clauses = []
    for raw in specifier.split(","):
        clause = raw.strip()
        if not clause:
            continue
        match = CLAUSE_RE.match(clause)
        if match is None:
            raise RequirementError(f"unsupported version clause '{clause}'")
        operator, version = match.group(1), match.group(2)
        if "*" in version and operator != "==":
            raise RequirementError(f"unsupported wildcard clause '{clause}'")
        if operator == "===":
            clauses.append(f"=={version}")
        elif operator == "~=":
            clauses.append(compatible_release(version))
        elif operator == "==" and version.endswith(".*"):
            # conda spells prefix matching as a bare glob, not '==1.2.*'.
            clauses.append(version)
        else:
            clauses.append(f"{operator}{version}")
    return ",".join(clauses)


def conda_requirement(requirement: str, name_map: dict, overrides: dict) -> str:
    """Translate one PEP 508 requirement string into a conda dependency."""
    match = REQUIREMENT_RE.match(requirement)
    if match is None:
        raise RequirementError(f"cannot parse requirement '{requirement}'")

    key = normalize(match.group("name"))
    if key in overrides:
        return overrides[key]

    if match.group("marker"):
        raise RequirementError(
            f"'{requirement}' carries an environment marker, which conda cannot "
            f"express; add an entry for '{key}' to "
            f"[tool.conda-recipe].requirement-overrides"
        )
    if match.group("extras"):
        raise RequirementError(
            f"'{requirement}' requests extras, which conda cannot express; add an "
            f"entry for '{key}' to [tool.conda-recipe].requirement-overrides"
        )

    name = name_map.get(match.group("name"), name_map.get(key, key))
    constraint = convert_specifier(match.group("specifier"))
    return f"{name} {constraint}" if constraint else name


# --------------------------------------------------------------------------
# Minimal YAML emitter
# --------------------------------------------------------------------------

# Plain (unquoted) style is only used for values we can prove are unambiguous:
# a package name optionally followed by a conda version constraint.
PLAIN_SAFE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]*"
    r"(?: [<>=!~*][^\s]*| [0-9][^\s]*)?$"
)


def scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if PLAIN_SAFE_RE.match(text):
        return text
    # Single-quoted style has no escape sequences at all, so paths and other
    # backslash-bearing values survive intact.
    return "'" + text.replace("'", "''") + "'"


def literal_block(text: str, indent: int, parent_indent: int) -> str:
    """Render ``text`` as a YAML literal block scalar indented by ``indent``."""
    lines = text.rstrip("\n").split("\n")
    first = next((line for line in lines if line.strip()), "")
    # A block whose first content line is itself indented needs an explicit
    # indentation indicator, or YAML infers the wrong block indent.
    header = f"|{indent - parent_indent}" if first[:1].isspace() else "|"
    pad = " " * indent
    body = "\n".join(pad + line if line.strip() else "" for line in lines)
    return f"{header}\n{body}"


def emit(node: dict, indent: int = 0) -> list[str]:
    """Render a dict/list/scalar tree as YAML lines."""
    lines: list[str] = []
    pad = " " * indent
    for key, value in node.items():
        if value is None or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.extend(emit(value, indent + 2))
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:")
            for item in value:
                if isinstance(item, dict):
                    # Nested mapping in a sequence: render it two levels in,
                    # then graft the "- " marker onto its first line.
                    block = emit(item, indent + 4)
                    block[0] = f"{pad}  - {block[0].lstrip()}"
                    lines.extend(block)
                else:
                    lines.append(f"{pad}  - {scalar(item)}")
        elif isinstance(value, str) and "\n" in value:
            lines.append(f"{pad}{key}: {literal_block(value, indent + 2, indent)}")
        else:
            lines.append(f"{pad}{key}: {scalar(value)}")
    return lines


# --------------------------------------------------------------------------
# Recipe construction
# --------------------------------------------------------------------------


def load_pyproject(project_dir: Path) -> dict:
    path = project_dir / "pyproject.toml"
    if not path.is_file():
        raise SystemExit(f"no pyproject.toml in {project_dir}")
    return tomllib.loads(path.read_text("utf-8"))


def find_sdist(dist_dir: Path, version: str) -> Path:
    matches = sorted(dist_dir.glob("*.tar.gz"))
    if not matches:
        raise SystemExit(f"No sdist found in {dist_dir}. Build one first.")
    for candidate in matches:
        if candidate.name.endswith(f"-{version}.tar.gz"):
            return candidate
    names = ", ".join(path.name for path in matches)
    raise SystemExit(f"No sdist for version {version} in {dist_dir} (found: {names})")


def extract_sdist(sdist: Path, destination: Path) -> None:
    """Unpack the sdist so its single top-level directory becomes ``destination``.

    rattler-build's ``path`` source copies a directory; it does not unpack
    archives, so the recipe points at the extracted tree.
    """
    staging = destination.parent / f"{destination.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    with tarfile.open(sdist) as archive:
        try:
            archive.extractall(staging, filter="data")
        except TypeError:  # Python < 3.12 has no extraction filters
            archive.extractall(staging)  # noqa: S202 - sdist we built ourselves

    roots = [child for child in staging.iterdir() if child.is_dir()]
    if len(roots) != 1:
        raise SystemExit(f"{sdist.name} does not contain a single root directory")

    shutil.rmtree(destination, ignore_errors=True)
    roots[0].rename(destination)
    shutil.rmtree(staging, ignore_errors=True)


def readme_text(project_dir: Path, readme) -> str | None:
    if isinstance(readme, dict):
        readme = readme.get("file")
    if not isinstance(readme, str):
        return None
    path = project_dir / readme
    return path.read_text("utf-8") if path.is_file() else None


def build_recipe(
    project_dir: Path, sdist: Path, output_dir: Path, build_number: int
) -> Path:
    pyproject = load_pyproject(project_dir)
    project = pyproject["project"]
    config = pyproject.get("tool", {}).get("conda-recipe", {})

    if "version" not in project:
        raise SystemExit(
            "the conda recipe needs a static [project] version; dynamic versions "
            "are not supported"
        )
    version = project["version"]
    if not sdist.name.endswith(f"-{version}.tar.gz"):
        raise SystemExit(
            f"sdist {sdist.name} does not match project version {version}; rebuild it"
        )

    name_map = config.get("name-map", {})
    overrides = {
        normalize(key): value
        for key, value in config.get("requirement-overrides", {}).items()
    }

    python_spec = f"python {project.get('requires-python', '')}".strip()
    build_requires = pyproject.get("build-system", {}).get("requires", [])

    try:
        host = [python_spec, "pip"] + [
            conda_requirement(req, name_map, overrides) for req in build_requires
        ]
        run = [python_spec] + [
            conda_requirement(req, name_map, overrides)
            for req in project.get("dependencies", [])
        ]
    except RequirementError as error:
        raise SystemExit(f"conda recipe: {error}") from None
    run += list(config.get("extra-run", []))

    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    extract_sdist(sdist, output_dir / "src")

    license_files = []
    for pattern in project.get("license-files", []):
        for path in sorted(project_dir.glob(pattern)):
            shutil.copy2(path, output_dir / path.name)
            license_files.append(path.name)

    license_expression = project.get("license")
    if isinstance(license_expression, dict):  # pre-PEP 639 table form
        license_expression = license_expression.get("text")

    urls = project.get("urls", {})
    noarch = config.get("noarch", True)

    build: dict = {"number": build_number}
    if noarch:
        build["noarch"] = "python"
    # The host environment's python is first on PATH during the build script.
    build["script"] = "python -m pip install . -vv --no-deps --no-build-isolation"

    test_imports = list(config.get("test-imports", []))
    tests = [{"python": {"imports": test_imports, "pip_check": True}}] if test_imports else []

    recipe = {
        "package": {
            "name": normalize(project["name"]),
            "version": version,
        },
        # A path source, not a url: rattler-build copies directories but does
        # not unpack archives, so the sdist is extracted beside this file.
        "source": {"path": "src"},
        "build": build,
        "requirements": {"host": host, "run": run},
        "tests": tests,
        "about": {
            "homepage": urls.get("Homepage") or urls.get("Repository"),
            "repository": urls.get("Repository"),
            "documentation": urls.get("Documentation"),
            "license": license_expression,
            "license_file": license_files[0] if len(license_files) == 1 else license_files,
            "summary": project.get("description"),
            "description": readme_text(project_dir, project.get("readme")),
        },
        "extra": {"recipe-maintainers": list(config.get("maintainers", []))},
    }

    recipe_path = output_dir / "recipe.yaml"
    recipe_path.write_text(
        "\n".join(
            [
                "# Generated from pyproject.toml by python-publish/scripts/conda_recipe.py.",
                "# Do not edit by hand: edit pyproject.toml and regenerate.",
                *emit(recipe),
                "",
            ]
        ),
        encoding="utf-8",
    )
    self_check(recipe_path)
    return recipe_path


def self_check(recipe: Path) -> None:
    """Re-parse the emitted recipe, when PyYAML happens to be importable."""
    try:
        import yaml
    except ModuleNotFoundError:
        return
    try:
        yaml.safe_load(recipe.read_text("utf-8"))
    except yaml.YAMLError as error:  # pragma: no cover - emitter bug
        raise SystemExit(f"generated {recipe} is not valid YAML: {error}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path("."),
        help="Directory holding the pyproject.toml to translate (default: .)",
    )
    parser.add_argument(
        "--sdist",
        type=Path,
        help="Source distribution to build from (default: newest matching <project-dir>/dist/*.tar.gz)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Directory to write the recipe into (default: <project-dir>/.conda-build/recipe)",
    )
    parser.add_argument(
        "--build-number",
        type=int,
        default=0,
        help="conda build number, for rebuilding an unchanged version (default: 0)",
    )
    parser.add_argument(
        "--get",
        metavar="KEY",
        help=(
            "Print one [tool.conda-recipe] value and exit, so shell callers do not "
            "need their own TOML parser (lists print space separated)"
        ),
    )
    args = parser.parse_args(argv)

    project_dir = args.project_dir.resolve()

    if args.get:
        value = load_pyproject(project_dir).get("tool", {}).get("conda-recipe", {}).get(args.get)
        if isinstance(value, list):
            print(" ".join(str(item) for item in value))
        elif value is not None:
            print(value)
        return 0

    if args.sdist is None:
        version = load_pyproject(project_dir)["project"]["version"]
        args.sdist = find_sdist(project_dir / "dist", version)
    if not args.sdist.is_file():
        raise SystemExit(f"sdist not found: {args.sdist}")

    output = args.output or project_dir / ".conda-build" / "recipe"
    print(build_recipe(project_dir, args.sdist.resolve(), output.resolve(), args.build_number))
    return 0


if __name__ == "__main__":
    sys.exit(main())
