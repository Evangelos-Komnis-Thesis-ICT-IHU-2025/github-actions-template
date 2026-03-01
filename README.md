# GitHub Template Repository Local Setup

`template_repo_agent.py` is an interactive wizard that runs **locally** inside a
repository created via GitHub's **Use this template** button.

## Workflow

1. Create a new repository from this template in GitHub.
2. Clone the new repository locally.
3. Run the setup wizard locally.
4. Keep only the generated project files (the setup script can auto-delete itself).

## What it does locally

- Prompts for setup options:
  - Repository name
  - Description
  - Copyright owner
  - Primary language
  - Labels to configure
  - Actions to enable (Release, Changelog, Lint, Labeler)
  - Semantic-release options (enable yes/no, default bump type)
  - Optional lint command overrides
- Generates repository files in place:
  - `README.md`
  - `LICENSE`
  - `.gitignore`
  - `.github/workflows/*.yml` (based on selected actions)
  - `.github/labeler-config.yml` (if Labeler enabled)
  - `.github/labels.json`
  - `.releaserc.json` (if semantic-release enabled)
- Optionally creates a local git commit with all setup changes.
- Optionally removes `template_repo_agent.py` after setup.

## Requirements

- Python 3.9+
- `git` installed and available in `PATH`

No GitHub token and no GitHub API permissions are required.

## Run

```bash
python3 template_repo_agent.py
```

## Notes

- This setup is local-only; it does not create repositories or labels via GitHub API.
- Labels are exported to `.github/labels.json` as a manifest for your own import/sync flow.
- If your OS blocks deleting a running script, remove `template_repo_agent.py` manually after setup.
