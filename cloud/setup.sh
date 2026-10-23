#!/usr/bin/env bash
# Set LINKEDIN_INSTALL_RESUME_TOOLS=1 to install the PDF toolchain on Debian/Ubuntu.
# No tracing: the environment may contain private candidate text.
set -euo pipefail
cloud_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${LINKEDIN_INSTALL_RESUME_TOOLS:-0}" == "1" ]]; then
  cloud_apt=()
  if [[ "$(id -u)" != "0" ]]; then cloud_apt=(sudo); fi
  "${cloud_apt[@]}" apt-get update -qq
  "${cloud_apt[@]}" apt-get install -y --no-install-recommends \
    latexmk texlive-latex-base texlive-latex-extra texlive-fonts-recommended poppler-utils
fi
python3 "$cloud_repo_root/skills/daily-linkedin-prep/scripts/private_sources.py" prepare
printf '%s\n' 'Private sources prepared. Browser access and prior application history still require verification.'
