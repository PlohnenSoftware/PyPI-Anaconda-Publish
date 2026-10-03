# PyPI and Anaconda Publish

[![Test](https://github.com/PlohnenSoftware/PyPI-Anaconda-Publish/actions/workflows/test.yml/badge.svg)](https://github.com/PlohnenSoftware/PyPI-Anaconda-Publish/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Build a Python package **once** and publish it to both [PyPI](https://pypi.org)
and [anaconda.org](https://anaconda.org) from a single tag.

- **No PyPI token anywhere.** PyPI uses trusted publishing (OIDC).
- **No conda installation.** The conda package is built by
  [rattler-build](https://rattler-build.prefix.dev), a single static binary — no
  Miniforge, no `conda install`, no `anaconda-client`.
- **No duplicated metadata.** The conda recipe is generated from
  `pyproject.toml`, so the conda package cannot drift from the PyPI one.
- **One sdist feeds both**, so the two packages are identical at the source level.

```yaml
jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write # required: mints the OIDC token for PyPI
      contents: read
    steps:
      - uses: actions/checkout@v5
      - uses: PlohnenSoftware/PyPI-Anaconda-Publish@v2
        with:
          test: pytest
          python_versions: auto
          tag: ${{ github.ref_type == 'tag' && github.ref_name || '' }}
          anaconda_owner: your-channel
          anaconda_api_key: ${{ secrets.ANACONDA_API_KEY }}
          dry_run: ${{ github.event_name == 'pull_request' }}
```

Leave `anaconda_owner` unset to publish to PyPI only; rattler-build is then
never downloaded.

---

## Usage

Every example below is **synthetic** — the package names, channel names and
URLs are placeholders. Swap in your own.

### A complete, minimal release

Build and publish a pure-Python project to PyPI on every tag, no conda:

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags: ["v*.*.*"]

permissions:
  contents: read

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write          # mints the OIDC token for PyPI
      contents: read
    steps:
      - uses: actions/checkout@v5
      - uses: PlohnenSoftware/PyPI-Anaconda-Publish@v2
        with:
          test: pytest
          tag: ${{ github.ref_name }}
```

That is the whole job. The version is read from `pyproject.toml`; the tag only
triggers the run and is checked against that version before anything is built.

### What the action does, in order

1. **`prepare`** (optional) — generate the project, before any metadata is read
2. **Verify** — the version against a tag and against a module's `__version__`
3. **Test** — across every Python version the project supports, in one job
4. **Build** — wheel + sdist, then `twine check --strict`
5. **Conda** (optional) — generate the recipe from `pyproject.toml`, build with
   rattler-build
6. **Publish** — PyPI via OIDC and anaconda.org, independently

Everything that can fail cheaply happens in steps 1–5, so nothing is uploaded
unless every artifact already exists and passes its checks. That matters
because a PyPI version number is burned the moment it is uploaded and can never
be reused.

#### The two uploads are independent

Neither upload can stop the other from being attempted. Each records its own
outcome and the job fails afterwards if either did:

| PyPI | anaconda.org | Job |
|---|---|---|
| ✅ | ✅ | passes |
| ❌ | ✅ | fails — but the conda package is published |
| ✅ | ❌ | fails — re-upload the conda package by hand |
| skipped | ✅ | passes |

By the time uploads start, only network calls remain, so a failure in one says
nothing about the other. Letting the anaconda.org upload proceed after a PyPI
failure costs nothing — conda uploads are replaceable — and saves a manual
re-upload.

### Choosing what to build

**`project_dir`** (default `.`) — the directory holding the `pyproject.toml` to
build, relative to the repository root. Point it at a sub-package in a monorepo.

```yaml
with:
  project_dir: packages/core
```

**`prepare`** (default empty) — a shell command run from the repository root
*before* any metadata is read. Use it to create the project that `project_dir`
points at: stub generators, codegen, vendoring, a slimmed runtime package.

```yaml
with:
  prepare: uv run codegen build --out .build/generated
  project_dir: .build/generated
```

### Verifying the version before anything builds

The published version always comes from `[project] version` in
`pyproject.toml` — and it must be **static** (dynamic versions are rejected).
Two optional checks catch mismatches *before* a build burns a version number:

**`tag`** (default empty) — a tag the version must equal. A leading `v` is
stripped, so `v1.4.0` matches version `1.4.0`.

```yaml
with:
  tag: ${{ github.ref_type == 'tag' && github.ref_name || '' }}
```

Tagging `v1.4.0` while the file still says `1.3.0` re-publishes `1.3.0`, which
PyPI rejects as a duplicate — after the build, when finding out is no longer
useful. Passing `tag:` turns that into an immediate, legible failure.

**`version_module`** (default empty) — a Python file whose `__version__` must
equal the project version, to catch drift between the metadata and the code.

```yaml
with:
  version_module: src/myproject/__init__.py
```

### Testing

**`test`** (default empty) — the bare command run via `uv run` inside
`project_dir`. Leave it empty to skip testing entirely. Give the command, not
the runner: `pytest`, not `uv run pytest`.

```yaml
with:
  test: pytest
```

**`python_versions`** (default empty) — which Python versions to run `test`
against:

| Value | Effect |
|---|---|
| empty | run once on the runner's default interpreter |
| `auto` | intersect `requires-python` with the stable CPython releases uv can fetch |
| `"3.10 3.13"` | test exactly those versions |

```yaml
with:
  test: pytest
  python_versions: auto
```

This runs inside **one job** — uv downloads interpreters on demand — so there is
no build matrix, which matters because a matrix would mean a reusable workflow,
and reusable workflows cannot use PyPI trusted publishing (see *Setup*). See
[Python version support](#python-version-support) for the details.

### Choosing what gets published

The two publishers are independent; each can be turned off on its own.

**`publish_pypi`** (default `true`) — upload the wheel and sdist to PyPI via
trusted publishing (OIDC). Set it `false` to publish to anaconda.org only —
useful before a PyPI trusted publisher exists.

```yaml
with:
  publish_pypi: false
```

**`pypi_publish_url`** (default empty) — an alternative index URL, for
rehearsing on TestPyPI.

```yaml
with:
  pypi_publish_url: https://test.pypi.org/legacy/
```

**`anaconda_owner`** (default empty) — the anaconda.org user or organisation to
upload to. **Leaving it empty skips conda entirely**: rattler-build is never
downloaded and no conda package is built.

```yaml
with:
  anaconda_owner: my-channel
```

**`anaconda_label`** (default `main`) — the label to upload under, e.g. `dev`
for a pre-release channel.

```yaml
with:
  anaconda_label: dev
```

**`anaconda_api_key`** (default empty) — the anaconda.org token. Required
whenever `anaconda_owner` is set and `dry_run` is false.

```yaml
with:
  anaconda_api_key: ${{ secrets.ANACONDA_API_KEY }}
```

**`build_number`** (default `0`) — the conda build number, for republishing an
unchanged version as a fresh build.

```yaml
with:
  build_number: 1
```

**`dry_run`** (default `false`) — build, test and check everything, upload
nothing. This is the cheapest way to find metadata errors before a tag exists.

```yaml
with:
  dry_run: ${{ github.event_name == 'pull_request' }}
```

### Pinning the toolchain

**`rattler_build_version`** (default `latest`) — the rattler-build release tag
to fetch, or `latest`. Pin it for reproducibility.

```yaml
with:
  rattler_build_version: v0.8.0
```

### Conda recipe: `[tool.conda-recipe]`

Optional. This table holds only what conda needs that `pyproject.toml` cannot
already express; everything else (name, version, dependencies, license) is read
from `[project]`. It lives in `pyproject.toml`:

```toml
[tool.conda-recipe]
noarch = true                      # false for compiled extensions
build-channels = ["conda-forge"]   # where dependencies come from, not where you publish
maintainers = ["some-handle"]
test-imports = ["myproject"]       # imported in the conda test env, with pip check
name-map = { PyYAML = "pyyaml" }   # PyPI name -> conda-forge name, where they differ
requirement-overrides = {}         # escape hatch for markers/extras conda cannot express
extra-run = []                     # conda-only runtime requirements
```

**`noarch`** (default `true`) — mark the package `noarch: python`, so a single
build serves every platform. Set `false` for compiled extensions, and matrix the
calling job over runners (see [Architecture support](#architecture-support)).

**`build-channels`** (default `["conda-forge"]`) — where build *dependencies*
come from, not where you publish.

**`maintainers`** — recorded as `recipe-maintainers` in the generated recipe.

**`test-imports`** — modules imported in the conda package's test environment,
followed by a `pip check`.

**`name-map`** — map a PyPI name to its conda-forge name where they differ.
Either spelling works as a key.

**`requirement-overrides`** — escape hatch for requirements conda cannot
express (environment markers, extras, direct URLs). A requirement whose
normalized name matches a key here is substituted verbatim and the rest of the
translation is skipped.

**`extra-run`** — runtime requirements to add to the conda package only.

Dependency translation **fails loudly** rather than silently dropping anything.
Environment markers, extras and direct URL requirements have no conda
equivalent, so they raise an error naming the requirement and pointing at
`requirement-overrides`. `~=` expands to conda's two-sided form
(`~=1.4.5` → `>=1.4.5,<1.5.0a0`), and names are normalised per PEP 503.

### Putting it together

PyPI + conda, tested across every supported version, rehearsed on PRs:

```yaml
- uses: PlohnenSoftware/PyPI-Anaconda-Publish@v2
  with:
    test: pytest
    python_versions: auto
    version_module: src/myproject/__init__.py
    tag: ${{ github.ref_type == 'tag' && github.ref_name || '' }}
    anaconda_owner: my-channel
    anaconda_api_key: ${{ secrets.ANACONDA_API_KEY }}
    dry_run: ${{ github.event_name == 'pull_request' }}
```

### Input reference

| Input | Default | Meaning |
|---|---|---|
| `project_dir` | `.` | Directory holding the pyproject.toml to build |
| `prepare` | — | Shell command run from the repo root before anything else |
| `test` | — | Command run via `uv run`; empty skips testing |
| `python_versions` | — | Versions to test against, or `auto` |
| `version_module` | — | File whose `__version__` must match the project version |
| `tag` | — | Tag the version must match, e.g. `v1.2.3` |
| `publish_pypi` | `true` | Publish to PyPI |
| `pypi_publish_url` | — | Alternative index, e.g. TestPyPI |
| `anaconda_owner` | — | anaconda.org owner; **empty skips conda entirely** |
| `anaconda_label` | `main` | anaconda.org label |
| `anaconda_api_key` | — | anaconda.org token |
| `build_number` | `0` | conda build number |
| `dry_run` | `false` | Build and check, upload nothing |
| `rattler_build_version` | `latest` | Pin rattler-build |

| Output | Meaning |
|---|---|
| `version` | The project version that was built |

---

## Architecture support

**Pure-Python projects are built once, and that single build is valid on every
architecture.** No matrix, no cross-compilation, no per-architecture upload:

| Artifact | Tag | Covers |
|---|---|---|
| wheel | `py3-none-any` | every OS and architecture |
| conda package | `noarch: python` | `linux-64`, `linux-aarch64`, `linux-ppc64le`, `osx-64`, `osx-arm64`, `win-64`, … |

The conda package lands in the channel's `noarch/` folder and conda serves it to
every platform. Per rattler-build: *"Packages marked as `noarch: python` contain
only pure Python code without compiled extensions. These packages work across
all Python versions and platforms from a single build."*

**The action itself** runs on any runner. rattler-build is fetched as the static
binary matching the host — Linux x86_64 / aarch64 / ppc64le, macOS x86_64 /
arm64, Windows x86_64 / aarch64 — and verified against the published `.sha256`.

### If you ship compiled extensions

`noarch` stops applying, because a compiled package is per-platform:

1. Set `noarch = false` in `[tool.conda-recipe]`.
2. Matrix the **calling job** over runners. Each run builds and uploads its own
   platform's artifact under the same package name, which is how anaconda.org
   expects multi-platform packages to arrive.

```yaml
jobs:
  publish:
    strategy:
      matrix:
        runner: [ubuntu-latest, ubuntu-24.04-arm, macos-latest, macos-13, windows-latest]
    runs-on: ${{ matrix.runner }}
```

For ABI3 extensions there is a middle option — one build per platform still
covering every Python version. See rattler-build's `version_independent`.

---

## Python version support

Two questions with two different answers.

**Which versions can install it?** Everything `requires-python` allows, from the
same single artifact. A `py3-none-any` wheel and a `noarch: python` conda package
both carry the floor as a constraint (`python >=3.10`) rather than being built
once per version. Nothing to configure.

**Which versions are actually tested?** That is `python_versions`:

```yaml
with:
  test: pytest
  python_versions: auto
```

`auto` reads `requires-python`, intersects it with the stable CPython releases
uv can fetch — betas and free-threaded builds filtered out — and runs the test
command on each. Today `>=3.10` expands to `3.10 3.11 3.12 3.13 3.14`.

This happens **inside one job**, because uv downloads interpreters on demand. No
build matrix — which matters, because a matrix would mean a reusable workflow,
and reusable workflows cannot use PyPI trusted publishing (see below).

Pin the list explicitly when you want a predictable release gate:

```yaml
with:
  python_versions: "3.10 3.14"   # floor and ceiling only
```

Empty tests once on the runner's default interpreter.

---

## Setup

### 1. PyPI trusted publisher

At <https://pypi.org/manage/account/publishing/> for a project that does not
exist yet, or the project's *Publishing* settings for one that does:

| Field | Value |
|---|---|
| Owner | your GitHub org or user |
| Repository | **your** repository, not this one |
| Workflow name | your caller's filename, e.g. `publish.yml` |
| Environment name | `pypi` |

Register the **caller**. This is a composite action, so its steps run inside your
job and the OIDC `job_workflow_ref` claim points at your workflow file.

> That is also why it is an action and not a reusable workflow. PyPI cannot match
> a reusable workflow's claims against a publisher config, so trusted publishing
> fails there with `invalid-publisher`
> ([warehouse#11096](https://github.com/pypi/warehouse/issues/11096)).

The workflow filename is load-bearing: renaming it breaks publishing with a 403
until the publisher config is updated to match.

The account that creates a pending publisher **becomes the owner** of the project
on first publish.

### 2. One GitHub environment

Create a single environment — `pypi` by convention — and use that same name in
the PyPI publisher config and in your job's `environment:`. It covers TestPyPI
too: register a publisher there with the same environment name and switch index
with `pypi_publish_url`. No second environment needed.

Restrict its deployment branches to the tag pattern `v*`. With no PyPI secret to
gate on, the OIDC claims are the only thing controlling who can publish, and the
environment name is one of those claims.

### 3. anaconda.org key

Only for repositories that publish conda packages. Create a token with **Allow
write access to the API site** at <https://anaconda.org/settings/access>, as a
user with upload rights to the target organisation, and add it as a repository
secret.

> The input is `anaconda_api_key`, not `..._token` — that is the name
> rattler-build reads.

---

## Requirements

- `pyproject.toml` with a **static** `[project] version`. Dynamic versions are
  rejected: the tag check and the conda recipe both need to read the version
  without building.
- `id-token: write` on the calling job, for PyPI.
- Nothing else. uv is installed by the action; Python comes from the runner.

## Contributing

`.github/workflows/test.yml` runs the action against `test/fixture` in dry-run
mode on Linux and macOS, covering the conda path, the PyPI-only path and the
generated-project path. Get it green before moving the `v1` tag — consumers pin
a moving major tag, so a bad push breaks every one of them at once.

Releases use **major tags only** — `v1`, `v2` — with no patch tags. The tag is
signed and moved forward for every compatible change:

```bash
git tag -f -s v1 -m "v1" && git push --force origin v1
```

A breaking input change means a new `v2`, leaving `v1` where it is.

## License

MIT — see [LICENSE](LICENSE). © PlohnenSoftware.
