#!/bin/bash
# Troca "uses: dono/repo@LATEST" pela release mais recente fixada por SHA: "uses: dono/repo@<sha> # vX.Y.Z".
# Uso: fixar-actions.sh arquivo.yml [outros.yml ...]   (requer gh autenticado)
set -euo pipefail
[ $# -gt 0 ] || { echo "uso: fixar-actions.sh arquivo.yml [...]"; exit 1; }
for arquivo in "$@"; do
  grep -oE 'uses: *[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@LATEST' "$arquivo" | sed -E 's/uses: *//; s/@LATEST//' | sort -u | while read -r acao; do
    tag=$(gh api "repos/$acao/releases/latest" --jq .tag_name)
    read -r tipo sha < <(gh api "repos/$acao/git/ref/tags/$tag" --jq '.object.type + " " + .object.sha')
    [ "$tipo" = "tag" ] && sha=$(gh api "repos/$acao/git/tags/$sha" --jq .object.sha)
    sed -i.bak -E "s#(uses: *)$acao@LATEST#\\1$acao@$sha \\# $tag#" "$arquivo" && rm -f "$arquivo.bak"
    echo "$arquivo: $acao -> $tag ($sha)"
  done
done
