"""Upload the web app to a Hugging Face Space (Docker SDK) from the workflow.

    HF_TOKEN=... HF_SPACE=user/air-raid-forecast python .github/scripts/deploy_space.py

Uses the Hub API (huggingface_hub.upload_folder) instead of `git push`: the Hub
rejects plain git pushes that contain binary files (figures, parquet) unless they
go through its large-file storage, which the API handles. Creates the Space if
needed and, when ALERTS_IN_UA_TOKEN is in the environment, sets it as a Space
secret. Tokens are only passed to the Hub client, never printed.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
# Everything the Docker image needs (see Dockerfile); data, models and figures are
# downloaded or trained by the app itself.
IGNORE = [".git/*", ".github/*", "data/*", "var/*", "models/*", "site/*", "live-data/*", "text-data/*",
          "**/__pycache__/*", "*.pyc", "*.png", "*.jpg", "*.parquet", "reports/figures/*", "docs/img/*",
          ".pytest_cache/*", "*.egg-info/*"]


def main() -> None:
    token, space = os.environ.get("HF_TOKEN"), os.environ.get("HF_SPACE")
    if not token or not space:
        sys.exit("HF_TOKEN (secret) and HF_SPACE (variable, e.g. user/air-raid-forecast) must both be set")
    api = HfApi(token=token)
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
    if alerts := os.environ.get("ALERTS_IN_UA_TOKEN"):
        api.add_space_secret(space, "ALERTS_IN_UA_TOKEN", alerts)
    with tempfile.TemporaryDirectory() as tmp:
        # the Space needs its own README with the Docker SDK front matter
        readme = Path(tmp) / "README.md"
        shutil.copy(ROOT / "deploy" / "huggingface" / "README.md", readme)
        api.upload_file(path_or_fileobj=str(readme), path_in_repo="README.md", repo_id=space, repo_type="space",
                        commit_message="README for the Space")
    sha = os.environ.get("GITHUB_SHA", "local")[:7]
    api.upload_folder(folder_path=str(ROOT), repo_id=space, repo_type="space", ignore_patterns=IGNORE + ["README.md"],
                      delete_patterns=["src/**", "scripts/**", "tests/**"], commit_message=f"Deploy {sha}")
    print(f"deployed {sha} to https://huggingface.co/spaces/{space}")


if __name__ == "__main__":
    main()
