#!/usr/bin/env bash
# Execute le pipeline sur le vrai compte AWS, pour une ou plusieurs parutions.
#
#   ./scripts/executer_sur_aws.sh 2026-09-22 2026-09-18 2026-09-17
#
# Ce script sert quand Docker n'est pas disponible sur le poste : il appelle les
# memes fonctions que les taches du graphe Airflow, dans le meme ordre, par
# `python -m radar.cli executer`. Quand Docker fonctionne, preferer
# `scripts/bascule_aws.sh`, qui passe par l'ordonnanceur.
#
# La cle est lue dans .env et n'est jamais affichee.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RACINE"

[ -f .env ] || { echo "ERREUR : pas de fichier .env" >&2; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a

export RADAR_BUCKET="${RADAR_BUCKET:-${S3_BUCKET:-}}"
export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
export AWS_DEFAULT_REGION="$AWS_REGION"
unset AWS_ENDPOINT_URL

[ -n "${AWS_ACCESS_KEY_ID:-}" ] || { echo "ERREUR : AWS_ACCESS_KEY_ID vide" >&2; exit 1; }
[ -n "${RADAR_BUCKET}" ] || { echo "ERREUR : compartiment non defini" >&2; exit 1; }

echo "Compartiment : ${RADAR_BUCKET} (region ${AWS_REGION})"
echo "Cle          : ${AWS_ACCESS_KEY_ID:0:4}**** (jamais affichee en entier)"
echo

mkdir -p results
journal="results/execution_aws.json"
echo "[" > "$journal"
premier=1

for jour in "$@"; do
  echo "===================== parution $jour ====================="
  debut=$(date +%s)
  if python -m uv run python -m radar.cli executer --jour "$jour" > "results/aws_$jour.log" 2>&1; then
    etat="reussi"
  else
    etat="bloque"
  fi
  fin=$(date +%s)
  tail -30 "results/aws_$jour.log"
  echo "-> $jour : $etat en $((fin - debut)) s"
  [ $premier -eq 0 ] && echo "," >> "$journal"
  premier=0
  printf '  {"jour": "%s", "etat": "%s", "duree_s": %d}' "$jour" "$etat" "$((fin - debut))" >> "$journal"
done

echo "" >> "$journal"
echo "]" >> "$journal"
echo
cat "$journal"
