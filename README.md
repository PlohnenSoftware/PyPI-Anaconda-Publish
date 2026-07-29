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
      - uses: PlohnenSoftware/PyPI-Anaconda-Publish@v1
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

## What it does

1. `prepare` — optional command that generates the project, for stub-only or
   otherwise synthesized distributions
2. **Verify** — the project version against a tag and against a module's
   `__version__`, before anything is built
3. **Test** — across every Python version the project supports, in one job
4. **Build** — wheel + sdist, then `twine check --strict`
5. **Conda** — generate the recipe from `pyproject.toml`, build with rattler-build
6. **Publish** — PyPI via OIDC, then anaconda.org

Every failure happens before the first upload. That matters because a PyPI
version number is burned the moment it is uploaded and can never be reused.

### The tag does not set the version

A tag only *triggers* a release; the published version comes from
`pyproject.toml`. Tagging `v0.3.0` while the file still says `0.2.0` re-publishes
`0.2.0`, which PyPI rejects as a duplicate — after the build, when finding out is
no longer useful. Pass `tag:` and that becomes an immediate, legible failure.

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

## Inputs

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

## `[tool.conda-recipe]`

Optional. Only what conda needs and `pyproject.toml` cannot already express:

```toml
[tool.conda-recipe]
noarch = true                      # false for compiled extensions
build-channels = ["conda-forge"]   # where dependencies come from, not where you publish
maintainers = ["your-handle"]
test-imports = ["your_package"]    # imported in the conda test env, with pip check
name-map = { PyYAML = "pyyaml" }   # PyPI name -> conda-forge name, where they differ
requirement-overrides = {}         # escape hatch for markers/extras conda cannot express
extra-run = []                     # conda-only runtime requirements
```

Dependency translation **fails loudly** rather than silently dropping anything.
Environment markers, extras and direct URL requirements have no conda
equivalent, so they raise an error naming the requirement and pointing at
`requirement-overrides`.

`~=` expands to conda's two-sided form (`~=1.4.5` → `>=1.4.5,<1.5.0a0`), and
names are normalised per PEP 503.

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

## Examples

### Package with tests, PyPI + conda

```yaml
- uses: PlohnenSoftware/PyPI-Anaconda-Publish@v1
  with:
    test: pytest
    python_versions: auto
    version_module: src/mypkg/__init__.py
    tag: ${{ github.ref_type == 'tag' && github.ref_name || '' }}
    anaconda_owner: my-channel
    anaconda_api_key: ${{ secrets.ANACONDA_API_KEY }}
    dry_run: ${{ github.event_name == 'pull_request' }}
```

### Generated package

`prepare` runs before anything is read, so it can create the project that
`project_dir` points at. Generated distributions — type stubs, slimmed runtime
packages, data-only distributions — behave exactly like checked-in ones.

```yaml
- uses: PlohnenSoftware/PyPI-Anaconda-Publish@v1
  with:
    prepare: uv run mytool generate-stubs
    project_dir: .build/stubs
    tag: ${{ github.ref_type == 'tag' && github.ref_name || '' }}
    dry_run: ${{ github.event_name == 'pull_request' }}
```

### Rehearse on TestPyPI

```yaml
- uses: PlohnenSoftware/PyPI-Anaconda-Publish@v1
  with:
    pypi_publish_url: https://test.pypi.org/legacy/
```

### Rehearse on every pull request

`dry_run: ${{ github.event_name == 'pull_request' }}` builds, tests and checks
everything — including the conda package — and uploads nothing. It is the
cheapest way to find metadata errors before a tag exists.

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

```bash
git tag -fa v1 -m "v1.2.0" && git push --force origin v1
```

A breaking input change means a new `v2`, leaving `v1` where it is.

## License

MIT — see [LICENSE](LICENSE). © PlohnenSoftware.
