#!/usr/bin/env python3
"""Local bootstrap wizard for repositories created from this GitHub template."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Dict, List, Optional, Sequence, Set, Tuple


ACTION_ORDER: List[Tuple[str, str]] = [
    ("release", "Release"),
    ("changelog", "Changelog"),
    ("lint", "Lint"),
    ("labeler", "Labeler"),
]
LANGUAGES = ["python", "javascript", "typescript", "go", "java", "rust", "generic"]
BUMP_TYPES = ["auto", "patch", "minor", "major"]

DEFAULT_LABELS = ["bug", "enhancement", "documentation"]

KNOWN_LABEL_COLORS = {
    "bug": "d73a4a",
    "documentation": "0075ca",
    "docs": "0075ca",
    "enhancement": "a2eeef",
    "feature": "a2eeef",
    "ci": "5319e7",
    "dependencies": "0366d6",
    "tests": "fbca04",
    "security": "b60205",
}

LABEL_PATTERN_HINTS = {
    "docs": ["docs/**", "**/*.md"],
    "documentation": ["docs/**", "**/*.md"],
    "ci": [".github/**"],
    "tests": ["tests/**", "**/*test*", "**/*spec*"],
    "dependencies": [
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "requirements*.txt",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
        "go.mod",
        "go.sum",
        "Cargo.toml",
        "Cargo.lock",
    ],
    "frontend": ["web/**", "frontend/**", "ui/**", "**/*.{js,jsx,ts,tsx,css,scss}"],
    "backend": ["api/**", "backend/**", "server/**"],
}

LINT_DEFAULTS = {
    "python": {
        "setup": "python",
        "version": "3.12",
        "install": "python -m pip install --upgrade pip ruff",
        "command": "ruff check .",
    },
    "javascript": {
        "setup": "node",
        "version": "20",
        "install": "npm ci --ignore-scripts || npm install --ignore-scripts",
        "command": "npm run lint --if-present",
    },
    "typescript": {
        "setup": "node",
        "version": "20",
        "install": "npm ci --ignore-scripts || npm install --ignore-scripts",
        "command": "npm run lint --if-present",
    },
    "go": {
        "setup": "go",
        "version": "1.22",
        "install": "",
        "command": "go vet ./...",
    },
    "java": {
        "setup": "java",
        "version": "21",
        "install": "",
        "command": "./gradlew check || mvn -B -DskipTests verify",
    },
    "rust": {
        "setup": "rust",
        "version": "stable",
        "install": "",
        "command": "cargo fmt --all -- --check && cargo clippy --all-targets --all-features -- -D warnings",
    },
    "generic": {
        "setup": "none",
        "version": "",
        "install": "",
        "command": "echo 'Set your lint command in .github/workflows/lint.yml' && exit 1",
    },
}

GITIGNORE_TEMPLATES = {
    "python": dedent(
        """\
        __pycache__/
        *.py[cod]
        *.pyo
        .Python
        .venv/
        venv/
        env/
        .pytest_cache/
        .ruff_cache/
        .mypy_cache/
        dist/
        build/
        """
    ),
    "javascript": dedent(
        """\
        node_modules/
        dist/
        build/
        .npm/
        npm-debug.log*
        yarn-debug.log*
        yarn-error.log*
        .pnpm-store/
        """
    ),
    "typescript": dedent(
        """\
        node_modules/
        dist/
        build/
        *.tsbuildinfo
        .npm/
        npm-debug.log*
        yarn-debug.log*
        yarn-error.log*
        .pnpm-store/
        """
    ),
    "go": dedent(
        """\
        bin/
        coverage.out
        *.test
        """
    ),
    "java": dedent(
        """\
        target/
        build/
        .gradle/
        *.class
        *.jar
        """
    ),
    "rust": dedent(
        """\
        target/
        """
    ),
    "generic": dedent(
        """\
        dist/
        build/
        """
    ),
}

COMMON_GITIGNORE = dedent(
    """\
    .DS_Store
    .idea/
    .vscode/
    *.swp
    *.swo
    """
)


@dataclass
class RepositoryContext:
    root: Path
    default_owner: str
    default_repo_name: str
    remote_url: Optional[str]


@dataclass
class WizardConfig:
    repository_name: str
    description: str
    owner: str
    language: str
    labels: List[str]
    actions: Set[str]
    semantic_release_enabled: bool
    release_bump_type: str
    lint_command: Optional[str]
    lint_install_command: Optional[str]
    create_commit: bool
    commit_message: str
    cleanup: bool
    source_url: Optional[str]


def run_command(
    command: Sequence[str], cwd: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and process.returncode != 0:
        cmd = " ".join(shlex.quote(part) for part in command)
        raise RuntimeError(
            f"Command failed ({cmd})\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return process


def prompt_required(prompt_text: str, validator=None) -> str:
    while True:
        value = input(prompt_text).strip()
        if not value:
            print("Value is required.")
            continue
        if validator and not validator(value):
            continue
        return value


def prompt_with_default(prompt_text: str, default: str, validator=None) -> str:
    while True:
        value = input(f"{prompt_text} [{default}]: ").strip()
        if not value:
            value = default
        if validator and not validator(value):
            continue
        return value


def prompt_yes_no(prompt_text: str, default: bool = True) -> bool:
    default_hint = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt_text} ({default_hint}): ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please enter 'y' or 'n'.")


def prompt_choice(prompt_text: str, choices: Sequence[str], default: str) -> str:
    lowered = {choice.lower(): choice for choice in choices}
    while True:
        value = input(f"{prompt_text} [{default}]: ").strip().lower()
        if not value:
            return default
        if value in lowered:
            return lowered[value]
        print(f"Invalid choice. Valid options: {', '.join(choices)}")


def prompt_list(prompt_text: str, default_values: Sequence[str]) -> List[str]:
    default_text = ", ".join(default_values)
    while True:
        raw = input(f"{prompt_text} [{default_text}]: ").strip()
        if not raw:
            values = list(default_values)
        else:
            values = [item.strip() for item in raw.split(",") if item.strip()]

        if values:
            seen = set()
            deduped = []
            for item in values:
                lowered = item.lower()
                if lowered not in seen:
                    deduped.append(item)
                    seen.add(lowered)
            return deduped
        print("Please provide at least one value.")


def prompt_actions() -> Set[str]:
    print("\nSelect actions to enable (comma-separated numbers or names).")
    for index, (_, label) in enumerate(ACTION_ORDER, start=1):
        print(f"  {index}. {label}")
    default = ",".join(str(i) for i in range(1, len(ACTION_ORDER) + 1))

    valid_by_index = {str(i): key for i, (key, _) in enumerate(ACTION_ORDER, start=1)}
    valid_by_name = {key: key for key, _ in ACTION_ORDER}

    while True:
        raw = input(f"Actions [{default}]: ").strip().lower()
        if not raw:
            return {key for key, _ in ACTION_ORDER}

        selected: Set[str] = set()
        invalid: List[str] = []
        for token in [part.strip() for part in raw.split(",") if part.strip()]:
            if token in valid_by_index:
                selected.add(valid_by_index[token])
            elif token in valid_by_name:
                selected.add(valid_by_name[token])
            else:
                invalid.append(token)

        if invalid:
            print(f"Invalid action selections: {', '.join(invalid)}")
            continue
        if not selected:
            print("Select at least one action.")
            continue
        return selected


def validate_repository_name(name: str) -> bool:
    if len(name) > 100:
        print("Repository name must be 100 characters or fewer.")
        return False
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        print("Repository name may contain only letters, digits, '.', '_' or '-'.")
        return False
    return True


def parse_github_remote(remote_url: str) -> Tuple[Optional[str], Optional[str]]:
    ssh_match = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    https_match = re.match(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
    if https_match:
        return https_match.group(1), https_match.group(2)

    return None, None


def is_git_repository(path: Path) -> bool:
    try:
        process = run_command(["git", "rev-parse", "--is-inside-work-tree"], path, check=False)
    except (FileNotFoundError, RuntimeError):
        return False
    return process.returncode == 0 and process.stdout.strip() == "true"


def read_git_config(path: Path, key: str) -> Optional[str]:
    try:
        process = run_command(["git", "config", "--get", key], path, check=False)
    except (FileNotFoundError, RuntimeError):
        return None
    if process.returncode != 0:
        return None
    value = process.stdout.strip()
    return value if value else None


def detect_repository_context(start_dir: Path) -> RepositoryContext:
    root = start_dir
    remote_url: Optional[str] = None

    if is_git_repository(start_dir):
        top_level = run_command(["git", "rev-parse", "--show-toplevel"], start_dir, check=False)
        if top_level.returncode == 0 and top_level.stdout.strip():
            root = Path(top_level.stdout.strip())
        remote_url = read_git_config(root, "remote.origin.url")

    owner_from_remote: Optional[str] = None
    repo_from_remote: Optional[str] = None
    if remote_url:
        owner_from_remote, repo_from_remote = parse_github_remote(remote_url)

    default_owner = owner_from_remote or read_git_config(root, "user.name") or "Your Organization"
    default_repo_name = repo_from_remote or root.name

    return RepositoryContext(
        root=root,
        default_owner=default_owner,
        default_repo_name=default_repo_name,
        remote_url=remote_url,
    )


def run_wizard(context: RepositoryContext) -> WizardConfig:
    print("Template Repository Local Setup Wizard")
    print("=" * 42)
    print("This setup runs locally in your repository (no GitHub API calls).\n")

    repository_name = prompt_with_default(
        "Repository name", context.default_repo_name, validate_repository_name
    )
    description = prompt_with_default("Repository description", f"{repository_name} project")
    owner = prompt_with_default("Copyright owner", context.default_owner)

    language = prompt_choice(
        "Primary language (python/javascript/typescript/go/java/rust/generic)",
        LANGUAGES,
        "python",
    )
    labels = prompt_list("Labels to configure (comma-separated)", DEFAULT_LABELS)
    actions = prompt_actions()

    semantic_release_enabled = False
    release_bump_type = "auto"
    if "release" in actions:
        semantic_release_enabled = prompt_yes_no("Enable semantic-release for release automation?", True)
        release_bump_type = prompt_choice(
            "Default release bump type (auto/patch/minor/major)", BUMP_TYPES, "auto"
        )

    lint_command: Optional[str] = None
    lint_install_command: Optional[str] = None
    if "lint" in actions:
        default_lint = LINT_DEFAULTS[language]["command"]
        lint_command = prompt_with_default("Lint command", default_lint)
        default_install = LINT_DEFAULTS[language]["install"]
        if default_install:
            lint_install_command = prompt_with_default(
                "Install command for lint dependencies (leave blank for default)",
                default_install,
            )
        else:
            lint_install_command = input(
                "Install command for lint dependencies (optional, leave blank to skip): "
            ).strip() or None

    create_commit = False
    commit_message = "chore: bootstrap repository from template"
    if is_git_repository(context.root):
        create_commit = prompt_yes_no("Create local git commit after setup?", True)
        if create_commit:
            commit_message = prompt_with_default("Commit message", commit_message)
    else:
        print("Git repository not detected. Skipping commit option.")

    cleanup = prompt_yes_no("Delete setup script after successful setup?", True)

    return WizardConfig(
        repository_name=repository_name,
        description=description,
        owner=owner,
        language=language,
        labels=labels,
        actions=actions,
        semantic_release_enabled=semantic_release_enabled,
        release_bump_type=release_bump_type,
        lint_command=lint_command,
        lint_install_command=lint_install_command,
        create_commit=create_commit,
        commit_message=commit_message,
        cleanup=cleanup,
        source_url=context.remote_url,
    )


def derive_label_patterns(label: str) -> List[str]:
    normalized = label.strip().lower()
    if normalized in LABEL_PATTERN_HINTS:
        return LABEL_PATTERN_HINTS[normalized]

    slug = re.sub(r"[^a-z0-9._-]+", "-", normalized).strip("-")
    if not slug:
        return ["**/*"]
    return [f"{slug}/**", f"**/{slug}/**"]


def label_color(label: str) -> str:
    normalized = label.strip().lower()
    if normalized in KNOWN_LABEL_COLORS:
        return KNOWN_LABEL_COLORS[normalized]
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:6]


def build_readme(config: WizardConfig) -> str:
    actions_enabled = sorted(config.actions)
    action_labels = {
        "release": "Release workflow",
        "changelog": "Changelog generation",
        "lint": "Lint checks",
        "labeler": "PR auto-labeling",
    }

    workflow_lines = [f"- {action_labels[action]}" for action in actions_enabled]
    workflow_block = "\n".join(workflow_lines) if workflow_lines else "- None"

    semantic_block = "Not enabled"
    if "release" in config.actions:
        semantic_block = (
            f"Enabled (default bump: {config.release_bump_type})"
            if config.semantic_release_enabled
            else f"Manual release via workflow_dispatch (default bump: {config.release_bump_type})"
        )

    label_lines = "\n".join(f"- `{label}`" for label in config.labels)
    source_url = config.source_url or "Set `origin` remote URL for this repository."

    return dedent(
        f"""\
        # {config.repository_name}

        {config.description}

        ## Repository Automation

        This repository was bootstrapped from a GitHub template with local setup.

        ## Enabled Workflows

        {workflow_block}

        ## Configuration Snapshot

        - Primary language: `{config.language}`
        - Semantic-release: {semantic_block}

        ## Labels

        {label_lines}

        ## Source Repository

        {source_url}
        """
    )


def build_license(config: WizardConfig) -> str:
    year = datetime.now().year
    return dedent(
        f"""\
        MIT License

        Copyright (c) {year} {config.owner}

        Permission is hereby granted, free of charge, to any person obtaining a copy
        of this software and associated documentation files (the "Software"), to deal
        in the Software without restriction, including without limitation the rights
        to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
        copies of the Software, and to permit persons to whom the Software is
        furnished to do so, subject to the following conditions:

        The above copyright notice and this permission notice shall be included in all
        copies or substantial portions of the Software.

        THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
        IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
        FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
        AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
        LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
        OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
        SOFTWARE.
        """
    )


def build_gitignore(language: str) -> str:
    return GITIGNORE_TEMPLATES.get(language, GITIGNORE_TEMPLATES["generic"]) + "\n" + COMMON_GITIGNORE


def build_labeler_workflow() -> str:
    return dedent(
        """\
        name: PR Labeler

        on:
          pull_request_target:
            types: [opened, synchronize, reopened]

        permissions:
          contents: read
          pull-requests: write

        jobs:
          label:
            runs-on: ubuntu-latest
            steps:
              - name: Label changed files
                uses: actions/labeler@v5
                with:
                  repo-token: "${{ secrets.GITHUB_TOKEN }}"
                  configuration-path: ".github/labeler-config.yml"
        """
    )


def build_labeler_config(labels: Sequence[str]) -> str:
    lines: List[str] = []
    for label in labels:
        safe_label = label.replace('"', '\\"')
        lines.append(f'"{safe_label}":')
        lines.append("  - changed-files:")
        lines.append("    - any-glob-to-any-file:")
        for pattern in derive_label_patterns(label):
            lines.append(f"      - '{pattern}'")
    return "\n".join(lines).strip() + "\n"


def build_semantic_release_config(bump_type: str) -> str:
    analyzer_config: Dict[str, object] = {"preset": "conventionalcommits"}
    if bump_type != "auto":
        analyzer_config["releaseRules"] = [{"type": "*", "release": bump_type}]

    config = {
        "branches": ["main"],
        "plugins": [
            ["@semantic-release/commit-analyzer", analyzer_config],
            "@semantic-release/release-notes-generator",
            ["@semantic-release/changelog", {"changelogFile": "CHANGELOG.md"}],
            [
                "@semantic-release/git",
                {
                    "assets": ["CHANGELOG.md"],
                    "message": "chore(release): ${nextRelease.version} [skip ci]\\n\\n${nextRelease.notes}",
                },
            ],
            "@semantic-release/github",
        ],
    }
    return json.dumps(config, indent=2) + "\n"


def build_semantic_release_workflow() -> str:
    return dedent(
        """\
        name: Release

        on:
          push:
            branches:
              - main

        permissions:
          contents: write
          issues: write
          pull-requests: write

        jobs:
          release:
            runs-on: ubuntu-latest
            steps:
              - name: Checkout
                uses: actions/checkout@v4
                with:
                  fetch-depth: 0

              - name: Setup Node.js
                uses: actions/setup-node@v4
                with:
                  node-version: "20"

              - name: Install semantic-release toolchain
                run: |
                  npm install --no-save \\
                    semantic-release \\
                    @semantic-release/changelog \\
                    @semantic-release/commit-analyzer \\
                    @semantic-release/git \\
                    @semantic-release/github \\
                    @semantic-release/release-notes-generator \\
                    conventional-changelog-conventionalcommits

              - name: Run semantic-release
                env:
                  GITHUB_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
                run: npx semantic-release
        """
    )


def build_manual_release_workflow(default_bump: str) -> str:
    bump_default = default_bump if default_bump != "auto" else "patch"
    return dedent(
        f"""\
        name: Release

        on:
          workflow_dispatch:
            inputs:
              bump:
                description: Version bump type
                required: true
                default: {bump_default}
                type: choice
                options:
                  - patch
                  - minor
                  - major

        permissions:
          contents: write

        jobs:
          release:
            runs-on: ubuntu-latest
            steps:
              - name: Checkout
                uses: actions/checkout@v4

              - name: Bump VERSION
                id: bump
                run: |
                  python - <<'PY'
                  import os

                  path = "VERSION"
                  if os.path.exists(path):
                      version = open(path, "r", encoding="utf-8").read().strip()
                  else:
                      version = "0.1.0"

                  major, minor, patch = [int(part) for part in version.split(".")]
                  bump = "${{{{ github.event.inputs.bump }}}}"

                  if bump == "major":
                      major, minor, patch = major + 1, 0, 0
                  elif bump == "minor":
                      minor, patch = minor + 1, 0
                  else:
                      patch += 1

                  new_version = f"{{major}}.{{minor}}.{{patch}}"
                  with open(path, "w", encoding="utf-8") as handle:
                      handle.write(new_version + "\\n")

                  with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
                      out.write(f"version={{new_version}}\\n")
                  PY

              - name: Commit and tag
                run: |
                  git config user.name "github-actions[bot]"
                  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
                  git add VERSION
                  git commit -m "chore(release): v${{{{ steps.bump.outputs.version }}}}" || echo "No commit needed"
                  git tag "v${{{{ steps.bump.outputs.version }}}}"
                  git push origin HEAD:main --follow-tags

              - name: Publish GitHub release
                uses: softprops/action-gh-release@v2
                with:
                  tag_name: "v${{{{ steps.bump.outputs.version }}}}"
                  generate_release_notes: true
        """
    )


def build_changelog_workflow() -> str:
    return dedent(
        """\
        name: Changelog

        on:
          push:
            branches:
              - main
          workflow_dispatch:

        permissions:
          contents: write

        jobs:
          changelog:
            runs-on: ubuntu-latest
            steps:
              - name: Checkout
                uses: actions/checkout@v4
                with:
                  fetch-depth: 0

              - name: Generate changelog
                uses: TriPSs/conventional-changelog-action@v5
                with:
                  github-token: "${{ secrets.GITHUB_TOKEN }}"
                  output-file: CHANGELOG.md
                  skip-version-file: true
                  skip-tag: true
                  skip-commit: false
                  git-message: "chore(changelog): update changelog [skip ci]"
        """
    )


def build_lint_workflow(config: WizardConfig) -> str:
    defaults = LINT_DEFAULTS[config.language]
    setup = defaults["setup"]
    version = defaults["version"]
    install = defaults["install"]
    command = defaults["command"]

    if config.lint_command:
        command = config.lint_command
    if config.lint_install_command is not None:
        install = config.lint_install_command

    matrix_block = "\n".join(
        [
            f"          - language: {config.language}",
            f"            setup: {setup}",
            f"            version: \"{version}\"",
            f"            install: {json.dumps(install)}",
            f"            lint: {json.dumps(command)}",
        ]
    )

    return (
        "name: Lint\n\n"
        "on:\n"
        "  push:\n"
        "    branches:\n"
        "      - main\n"
        "  pull_request:\n\n"
        "permissions:\n"
        "  contents: read\n\n"
        "jobs:\n"
        "  lint:\n"
        "    runs-on: ubuntu-latest\n"
        "    strategy:\n"
        "      fail-fast: false\n"
        "      matrix:\n"
        "        include:\n"
        f"{matrix_block}\n\n"
        "    steps:\n"
        "      - name: Checkout\n"
        "        uses: actions/checkout@v4\n\n"
        "      - name: Setup Python\n"
        "        if: matrix.setup == 'python'\n"
        "        uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: \"${{ matrix.version }}\"\n\n"
        "      - name: Setup Node.js\n"
        "        if: matrix.setup == 'node'\n"
        "        uses: actions/setup-node@v4\n"
        "        with:\n"
        "          node-version: \"${{ matrix.version }}\"\n\n"
        "      - name: Setup Go\n"
        "        if: matrix.setup == 'go'\n"
        "        uses: actions/setup-go@v5\n"
        "        with:\n"
        "          go-version: \"${{ matrix.version }}\"\n\n"
        "      - name: Setup Java\n"
        "        if: matrix.setup == 'java'\n"
        "        uses: actions/setup-java@v4\n"
        "        with:\n"
        "          distribution: temurin\n"
        "          java-version: \"${{ matrix.version }}\"\n\n"
        "      - name: Setup Rust\n"
        "        if: matrix.setup == 'rust'\n"
        "        uses: dtolnay/rust-toolchain@stable\n\n"
        "      - name: Install lint dependencies\n"
        "        if: matrix.install != ''\n"
        "        run: ${{ matrix.install }}\n\n"
        "      - name: Run lint\n"
        "        run: ${{ matrix.lint }}\n"
    )


def build_repository_files(config: WizardConfig) -> Dict[str, str]:
    files: Dict[str, str] = {
        "README.md": build_readme(config),
        "LICENSE": build_license(config),
        ".gitignore": build_gitignore(config.language),
        ".github/labels.json": json.dumps(
            [{"name": label, "color": label_color(label)} for label in config.labels],
            indent=2,
        )
        + "\n",
    }

    if "labeler" in config.actions:
        files[".github/workflows/labeler.yml"] = build_labeler_workflow()
        files[".github/labeler-config.yml"] = build_labeler_config(config.labels)

    if "release" in config.actions:
        if config.semantic_release_enabled:
            files[".github/workflows/release.yml"] = build_semantic_release_workflow()
            files[".releaserc.json"] = build_semantic_release_config(config.release_bump_type)
        else:
            files[".github/workflows/release.yml"] = build_manual_release_workflow(
                config.release_bump_type
            )
            files["VERSION"] = "0.1.0\n"

    if "changelog" in config.actions:
        files[".github/workflows/changelog.yml"] = build_changelog_workflow()
        files["CHANGELOG.md"] = "# Changelog\n\nAll notable changes to this project will be documented in this file.\n"

    if "lint" in config.actions:
        files[".github/workflows/lint.yml"] = build_lint_workflow(config)

    return files


def write_files(base_dir: Path, files: Dict[str, str]) -> None:
    for relative_path, content in files.items():
        destination = base_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def cleanup_setup_script(repo_root: Path) -> bool:
    script_path = Path(__file__).resolve()
    try:
        script_path.relative_to(repo_root.resolve())
    except ValueError:
        return False

    if not script_path.exists():
        return False

    try:
        script_path.unlink()
        return True
    except OSError as exc:
        print(f"Warning: Could not delete setup script '{script_path.name}': {exc}")
        return False


def create_local_commit(repo_root: Path, message: str) -> None:
    if not is_git_repository(repo_root):
        print("Git repository not detected; skipping commit.")
        return

    run_command(["git", "add", "-A"], repo_root)
    commit = run_command(["git", "commit", "-m", message], repo_root, check=False)
    output = f"{commit.stdout}\n{commit.stderr}".lower()
    if commit.returncode == 0:
        print("Created local commit with setup changes.")
        return

    if "nothing to commit" in output or "no changes added to commit" in output:
        print("No local changes to commit.")
        return

    cmd = "git commit -m <message>"
    raise RuntimeError(
        f"Command failed ({cmd})\nstdout:\n{commit.stdout}\nstderr:\n{commit.stderr}"
    )


def workflow_summary(config: WizardConfig) -> List[str]:
    summary: List[str] = []
    if "labeler" in config.actions:
        summary.append("Labeler workflow with .github/labeler-config.yml")
    if "release" in config.actions:
        if config.semantic_release_enabled:
            summary.append(
                f"Semantic-release workflow (.releaserc.json, default bump: {config.release_bump_type})"
            )
        else:
            summary.append(
                f"Manual release workflow (workflow_dispatch, default bump: {config.release_bump_type})"
            )
    if "changelog" in config.actions:
        summary.append("Conventional-commit changelog workflow")
    if "lint" in config.actions:
        summary.append(f"Lint workflow for {config.language}")
    return summary


def main() -> int:
    try:
        context = detect_repository_context(Path.cwd())
        config = run_wizard(context)

        files = build_repository_files(config)
        write_files(context.root, files)

        script_deleted = False
        if config.cleanup:
            script_deleted = cleanup_setup_script(context.root)

        if config.create_commit:
            create_local_commit(context.root, config.commit_message)

        print("\nRepository setup completed locally.")
        print("Enabled workflows/configuration:")
        for item in workflow_summary(config):
            print(f"- {item}")

        print("Configured labels manifest: .github/labels.json")
        if script_deleted:
            print("Cleanup: setup script deleted.")
        elif config.cleanup:
            print("Cleanup: setup script was not deleted automatically.")
        else:
            print("Cleanup: setup script kept (as requested).")

        return 0

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        return 1
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
