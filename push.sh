#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

REMOTE="${GIT_REMOTE:-origin}"
BRANCH="${GIT_BRANCH:-}"
MESSAGE=""
ASSUME_YES=0
PUSH_ONLY=0

usage() {
  cat <<'EOF'
使い方:
  ./push.sh [オプション] [コミットメッセージ]

例:
  ./push.sh "非同期連携の設計書を追加"
  ./push.sh --yes "ドキュメントを更新"
  ./push.sh --push-only

オプション:
  -y, --yes        確認を省略する
  --push-only      commitせず、既存commitだけをpushする
  -h, --help       この説明を表示する

環境変数:
  GIT_REMOTE       push先remote（既定: origin）
  GIT_BRANCH       pushするbranch（既定: 現在のbranch）
EOF
}

confirm() {
  local prompt="$1"
  local answer

  if [ "${ASSUME_YES}" -eq 1 ]; then
    return 0
  fi
  if [ ! -t 0 ]; then
    echo "確認入力ができません。自動実行では --yes を指定してください。" >&2
    return 1
  fi

  read -r -p "${prompt} [y/N]: " answer
  case "${answer}" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes)
      ASSUME_YES=1
      ;;
    --push-only)
      PUSH_ONLY=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      MESSAGE="${*:-}"
      break
      ;;
    -*)
      echo "不明なオプションです: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [ -n "${MESSAGE}" ]; then
        MESSAGE+=" $1"
      else
        MESSAGE="$1"
      fi
      ;;
  esac
  shift
done

if ! command -v git >/dev/null 2>&1; then
  echo "gitが見つかりません。" >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "このディレクトリはGitリポジトリではありません。" >&2
  exit 1
fi

if [ -z "${BRANCH}" ]; then
  BRANCH="$(git branch --show-current)"
fi
if [ -z "${BRANCH}" ]; then
  echo "現在のbranchを取得できません。GIT_BRANCHを指定してください。" >&2
  exit 1
fi
if ! git remote get-url "${REMOTE}" >/dev/null 2>&1; then
  echo "Git remote '${REMOTE}' がありません。" >&2
  echo "現在のremote:" >&2
  git remote -v >&2
  exit 1
fi

echo "Repository : $(pwd)"
echo "Remote     : ${REMOTE} ($(git remote get-url "${REMOTE}"))"
echo "Branch     : ${BRANCH}"
echo

if [ "${PUSH_ONLY}" -eq 0 ] && [ -n "$(git status --porcelain)" ]; then
  echo "変更ファイル:"
  git status --short
  echo

  if [ -z "${MESSAGE}" ]; then
    if [ -t 0 ]; then
      read -r -p "コミットメッセージ: " MESSAGE
    fi
    if [ -z "${MESSAGE}" ]; then
      MESSAGE="Update $(date '+%Y-%m-%d %H:%M:%S')"
    fi
  fi

  if ! confirm "表示した変更をすべてcommitしますか？"; then
    echo "中止しました。"
    exit 0
  fi

  if [ -z "$(git config user.name || true)" ] || [ -z "$(git config user.email || true)" ]; then
    echo "Gitのuser.nameまたはuser.emailが設定されていません。" >&2
    echo '例: git config user.name "Your Name"' >&2
    echo '例: git config user.email "you@example.com"' >&2
    exit 1
  fi

  git add -A
  if git diff --cached --quiet; then
    echo "commit対象の変更はありません。"
  else
    echo
    echo "Commit: ${MESSAGE}"
    git commit -m "${MESSAGE}"
  fi
elif [ "${PUSH_ONLY}" -eq 0 ]; then
  echo "未commitの変更はありません。"
else
  echo "--push-only: working treeの変更はcommitしません。"
fi

echo
echo "${REMOTE}/${BRANCH} へpushします。"

# VS Code終了後に残るAskPass socketを使わず、端末で認証できるようにする。
unset GIT_ASKPASS
unset SSH_ASKPASS
unset VSCODE_GIT_ASKPASS_NODE
unset VSCODE_GIT_ASKPASS_EXTRA_ARGS
unset VSCODE_GIT_ASKPASS_MAIN
export GIT_TERMINAL_PROMPT=1

if ! git push -u "${REMOTE}" "${BRANCH}"; then
  cat >&2 <<'EOF'

pushに失敗しました。
HTTPSでGitHubへ接続する場合、password欄にはGitHubのパスワードではなく
Personal Access Tokenを入力してください。認証情報はこのスクリプトへ保存しません。
EOF
  exit 1
fi

echo
echo "pushが完了しました。"
