#!/usr/bin/env python3
"""Interactive GitHub template repository bootstrapper.

This script creates a template repository in a GitHub organization and
initializes it with reusable GitHub Actions workflows based on user input.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from getpass import getpass
from pathlib import Path
from textwrap import dedent
from typing import Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import quote

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: requests. Install it with `pip install requests`."
    ) from exc


API_BASE = "https://api.github.com"
ACTION_ORDER: List[Tuple[str, str]] = [
    ("release", "Release"),
    ("changelog", "Changelog"),
    ("lint", "Lint"),
    ("labeler", "Labeler"),
]
LANGUAGES = ["python", "javascript", "typescript", "go", "java", "rust", "generic"]
BUMP_TYPES = ["auto", "patch", "minor", "major"]

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
class WizardConfig:
    token: str
    organization: str
    repository_name: str
    description: str
    visibility: str
    language: str
    labels: List[str]
    actions: Set[str]
    semantic_release_enabled: bool
    release_bump_type: str
    lint_command: Optional[str]
    lint_install_command: Optional[str]


class GitHubAPIError(RuntimeError):
    """Raised when GitHub API returns a non-success response."""


class GitHubClient:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "template-repo-agent/1.0",
            }
        )

    def create_org_repository(
        self,
        organization: str,
        name: str,
        description: str,
        private: bool,
        is_template: bool = True,
    ) -> dict:
        payload = {
            "name": name,
            "description": description,
            "private": private,
            "is_template": is_template,
            "auto_init": False,
        }
        response = self.session.post(f"{API_BASE}/orgs/{organization}/repos", json=payload, timeout=30)
        if response.status_code == 201:
            return response.json()

        if response.status_code == 422:
            details = _extract_api_error(response)
            if "name already exists" in details.lower() or "already exists" in details.lower():
                raise GitHubAPIError(
                    f"Repository '{organization}/{name}' already exists. Choose a different name."
                )
            raise GitHubAPIError(f"Validation failed while creating repository: {details}")

        if response.status_code == 404:
            raise GitHubAPIError(
                f"Organization '{organization}' not found or token lacks permission to create repositories."
            )

        raise GitHubAPIError(
            f"Failed to create repository (HTTP {response.status_code}): {_extract_api_error(response)}"
        )

    def create_label(self, organization: str, repository: str, name: str, color: str, description: str) -> None:
        payload = {
            "name": name,
            "color": color,
            "description": description,
        }
        response = self.session.post(
            f"{API_BASE}/repos/{organization}/{repository}/labels",
            json=payload,
            timeout=30,
        )
        if response.status_code in (200, 201):
            return
        if response.status_code == 422:
            # Duplicate label or invalid data.
            message = _extract_api_error(response)
            raise GitHubAPIError(f"Could not create label '{name}': {message}")
        raise GitHubAPIError(
            f"Failed to create label '{name}' (HTTP {response.status_code}): {_extract_api_error(response)}"
        )


def _extract_api_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or "Unknown API error"

    message = payload.get("message", "Unknown API error")
    errors = payload.get("errors")
    if errors:
        return f"{message} | details: {errors}"
    return message


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


def prompt_list(prompt_text: str) -> List[str]:
    while True:
        raw = input(prompt_text).strip()
        values = [item.strip() for item in raw.split(",") if item.strip()]
        if values:
            seen = set()
            deduped = []
            for item in values:
                if item.lower() not in seen:
                    deduped.append(item)
                    seen.add(item.lower())
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


def validate_organization(name: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9-]+", name):
        print("Organization name may contain only letters, digits and '-'.")
        return False
    return True


def run_wizard() -> WizardConfig:
    print("GitHub Template Repository Setup Wizard")
    print("=" * 40)

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        print("Using token from GITHUB_TOKEN environment variable.")
    else:
        token = getpass("GitHub token (requires repo + org permissions): ").strip()
        if not token:
            raise SystemExit("GitHub token is required.")

    default_org = os.environ.get("GITHUB_ORG", "")
    if default_org:
        organization = prompt_with_default("Organization", default_org, validate_organization)
    else:
        organization = prompt_required("Organization: ", validate_organization)

    repository_name = prompt_required("Repository name: ", validate_repository_name)
    description = prompt_with_default("Repository description", "GitHub automation template repository")
    visibility = prompt_choice("Visibility (public/private)", ["public", "private"], "private")
    language = prompt_choice(
        "Primary language (python/javascript/typescript/go/java/rust/generic)",
        LANGUAGES,
        "python",
    )

    labels = prompt_list("Labels to create (comma-separated): ")
    actions = prompt_actions()

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

    return WizardConfig(
        token=token,
        organization=organization,
        repository_name=repository_name,
        description=description,
        visibility=visibility,
        language=language,
        labels=labels,
        actions=actions,
        semantic_release_enabled=semantic_release_enabled,
        release_bump_type=release_bump_type,
        lint_command=lint_command,
        lint_install_command=lint_install_command,
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


def build_readme(config: WizardConfig, repository_url: str) -> str:
    actions_enabled = sorted(config.actions)
    action_labels = {
        "release": "Release workflow",
        "changelog": "Changelog generation",
        "lint": "Lint checks",
        "labeler": "PR auto-labeling",
    }

    workflow_lines = [f"- {action_labels[action]}" for action in actions_enabled]
    workflow_block = "\n".join(workflow_lines) if workflow_lines else "- None"

    semantic_block = "Disabled"
    if "release" in config.actions:
        semantic_block = (
            f"Enabled (default bump: {config.release_bump_type})"
            if config.semantic_release_enabled
            else f"Disabled (manual workflow_dispatch release, default bump: {config.release_bump_type})"
        )

    label_lines = "\n".join(f"- `{label}`" for label in config.labels)

    return dedent(
        f"""\
        # {config.repository_name}

        {config.description}

        ## Template Purpose

        This repository is configured as a GitHub Template Repository for quickly bootstrapping
        projects with reusable automation workflows.

        ## Enabled Workflows

        {workflow_block}

        ## Configuration Snapshot

        - Visibility: `{config.visibility}`
        - Primary language: `{config.language}`
        - Semantic-release: {semantic_block}

        ## Labels

        {label_lines}

        ## Usage

        1. Click **Use this template** in GitHub.
        2. Create a new repository from this template.
        3. Adjust workflow settings in `.github/workflows/` as needed.

        ## Source Repository

        {repository_url}
        """
    )


def build_license(config: WizardConfig) -> str:
    year = datetime.now().year
    owner = config.organization
    return dedent(
        f"""\
        MIT License

        Copyright (c) {year} {owner}

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


def build_repository_files(config: WizardConfig, repository_url: str) -> Dict[str, str]:
    files: Dict[str, str] = {
        "README.md": build_readme(config, repository_url),
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


def run_command(command: Sequence[str], cwd: Path) -> str:
    process = subprocess.run(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        cmd = " ".join(shlex.quote(part) for part in command)
        raise RuntimeError(
            f"Command failed ({cmd})\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return process.stdout.strip()


def initialize_git_repository(temp_dir: Path, remote_url: str) -> None:
    run_command(["git", "init"], temp_dir)

    # Normalize main branch across older/newer git versions.
    run_command(["git", "checkout", "-B", "main"], temp_dir)

    run_command(["git", "config", "user.name", "template-repo-agent"], temp_dir)
    run_command(
        ["git", "config", "user.email", "template-repo-agent@users.noreply.github.com"],
        temp_dir,
    )

    run_command(["git", "add", "."], temp_dir)
    run_command(["git", "commit", "-m", "chore: initialize template repository"], temp_dir)
    run_command(["git", "remote", "add", "origin", remote_url], temp_dir)
    run_command(["git", "push", "-u", "origin", "main"], temp_dir)


def create_labels(client: GitHubClient, config: WizardConfig) -> List[str]:
    created = []
    for label in config.labels:
        try:
            client.create_label(
                organization=config.organization,
                repository=config.repository_name,
                name=label,
                color=label_color(label),
                description="Template label",
            )
            created.append(label)
        except GitHubAPIError as exc:
            # Continue with other labels; report all failures after attempt.
            print(f"Warning: {exc}")
    return created


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
        config = run_wizard()
        client = GitHubClient(config.token)

        print("\nCreating repository...")
        repository = client.create_org_repository(
            organization=config.organization,
            name=config.repository_name,
            description=config.description,
            private=config.visibility == "private",
            is_template=True,
        )

        html_url = repository["html_url"]
        remote_url = (
            f"https://x-access-token:{quote(config.token, safe='')}@github.com/"
            f"{config.organization}/{config.repository_name}.git"
        )

        files = build_repository_files(config, html_url)
        with tempfile.TemporaryDirectory(prefix="template-repo-agent-") as temp_path:
            temp_dir = Path(temp_path)
            write_files(temp_dir, files)

            print("Creating initial commit and pushing files...")
            initialize_git_repository(temp_dir, remote_url)

        print("Creating labels...")
        created_labels = create_labels(client, config)

        print("\nRepository successfully created.")
        print(f"URL: {html_url}")
        print("Enabled workflows/configuration:")
        for item in workflow_summary(config):
            print(f"- {item}")

        if created_labels:
            print("Created labels:")
            for label in created_labels:
                print(f"- {label}")
        else:
            print("No labels were created.")

        return 0

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        return 1
    except (GitHubAPIError, RuntimeError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
