#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

REMOTE="${GIT_REMOTE:-origin}"
BRANCH="${GIT_BRANCH:-$(git branch --show-current)}"
MESSAGE="${*:-Update $(date '+%Y-%m-%d %H:%M:%S')}"

if [ -z "${BRANCH}" ]; then
  echo "現在のGitブランチを取得できません。" >&2
  exit 1
fi

echo "変更をステージします。"
git add -A

if git diff --cached --quiet; then
  echo "コミットする変更はありません。"
else
  echo "コミットします: ${MESSAGE}"
  git commit -m "${MESSAGE}"
fi

echo "${REMOTE}/${BRANCH}へプッシュします。"
git push -u "${REMOTE}" "${BRANCH}"

echo "完了しました。"
