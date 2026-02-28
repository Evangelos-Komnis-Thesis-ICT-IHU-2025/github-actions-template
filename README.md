# GitHub Template Repository Automation Agent

`template_repo_agent.py` is an interactive setup wizard that creates a GitHub **template repository** inside an organization and bootstraps it with configurable GitHub Actions workflows.

## What it does

- Prompts for setup options:
  - Repository name
  - Description
  - Visibility (public/private)
  - Primary language
  - Labels to create
  - Actions to enable (Release, Changelog, Lint, Labeler)
  - Semantic-release options (enable yes/no, default bump type)
  - Optional lint command overrides
- Creates the repository via GitHub API with `is_template: true`
- Generates and commits:
  - `README.md`
  - `LICENSE`
  - `.gitignore`
  - `.github/workflows/*.yml` (based on selected actions)
  - `.github/labeler-config.yml` (if Labeler enabled)
  - `.releaserc.json` (if semantic-release enabled)
- Pushes initial commit to `main`
- Creates labels in the new repository via GitHub API
- Prints the created repository URL and workflow summary

## Requirements

- Python 3.9+
- `git` installed and available in `PATH`
- Python package: `requests`

Install dependency:

```bash
pip install requests
```

## Authentication

Use a GitHub token with org repo creation permissions.

Recommended environment variables:

```bash
export GITHUB_TOKEN=your_token_here
export GITHUB_ORG=your_org_name
```

If `GITHUB_TOKEN` is not set, the script prompts for it securely.

## Run

```bash
python3 template_repo_agent.py
```

## Notes

- The generated lint workflow is language-aware through matrix/conditionals and can be overridden via wizard input.
- Labeler rules are auto-generated from label names, using sensible defaults for common labels (`docs`, `tests`, `ci`, `dependencies`) and fallback path rules for custom labels.
- If repository creation fails (for example duplicate name or permission issues), the script exits with a clear error message.
