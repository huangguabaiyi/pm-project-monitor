#!/usr/bin/env sh
set -eu

usage() {
  cat <<'USAGE'
Usage:
  deploy/update-from-github.sh [--check]
  deploy/update-from-github.sh --apply [--branch BRANCH] [--skip-backup] [--allow-dirty]

Checks whether the current Git branch has updates on origin, and optionally
updates the Docker Compose deployment.

Options:
  --check          Only check for remote updates. This is the default.
  --apply          Pull updates and rebuild/restart Docker Compose services.
  --branch BRANCH  Compare against origin/BRANCH. Defaults to current branch.
  --skip-backup    Do not run pg_dump before updating.
  --allow-dirty    Allow updating with local uncommitted files.
  -h, --help       Show this help.
USAGE
}

mode="check"
branch=""
skip_backup="false"
allow_dirty="false"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      mode="check"
      ;;
    --apply)
      mode="apply"
      ;;
    --branch)
      shift
      if [ "$#" -eq 0 ]; then
        echo "Missing value for --branch" >&2
        exit 2
      fi
      branch="$1"
      ;;
    --skip-backup)
      skip_backup="true"
      ;;
    --allow-dirty)
      allow_dirty="true"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
cd "$repo_dir"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "This directory is not a Git repository: $repo_dir" >&2
  exit 1
fi

if [ -z "$branch" ]; then
  branch=$(git branch --show-current)
fi

if [ -z "$branch" ]; then
  echo "Cannot determine current branch. Pass --branch BRANCH explicitly." >&2
  exit 1
fi

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    echo "Docker Compose is not available. Install Docker Compose v2 or docker-compose." >&2
    exit 1
  fi
}

echo "Repository: $repo_dir"
echo "Branch: $branch"
echo "Fetching origin/$branch ..."
git fetch origin "$branch"

local_ref=$(git rev-parse HEAD)
remote_ref=$(git rev-parse "origin/$branch")
base_ref=$(git merge-base HEAD "origin/$branch")

if [ "$local_ref" = "$remote_ref" ]; then
  echo "Already up to date."
  exit 0
fi

if [ "$local_ref" != "$base_ref" ]; then
  echo "Local branch is ahead of or diverged from origin/$branch." >&2
  echo "Resolve local commits first, or run with --allow-dirty only if you know this is safe." >&2
  exit 1
fi

echo "Remote updates are available:"
git log --oneline --decorate --max-count=12 "HEAD..origin/$branch"

if [ "$mode" = "check" ]; then
  echo
  echo "Run deploy/update-from-github.sh --apply to update and restart services."
  exit 0
fi

if [ "$allow_dirty" != "true" ] && [ -n "$(git status --porcelain)" ]; then
  echo "Working tree has uncommitted changes. Commit/stash them, or rerun with --allow-dirty." >&2
  git status --short >&2
  exit 1
fi

if [ "$skip_backup" != "true" ]; then
  backup_file="backup-$(date +%Y%m%d-%H%M%S).sql"
  echo "Backing up PostgreSQL to $backup_file ..."
  if ! compose exec -T db pg_dump -U monitor requirement_monitor > "$backup_file"; then
    rm -f "$backup_file"
    echo "Database backup failed. Fix Docker/PostgreSQL first, or rerun with --skip-backup." >&2
    exit 1
  fi
fi

echo "Pulling updates ..."
git pull --ff-only origin "$branch"

echo "Rebuilding and restarting services ..."
compose up -d --build

echo "Deployment status:"
compose ps
