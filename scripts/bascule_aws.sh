#!/usr/bin/env bash
# Bascule du radar de LocalStack vers le vrai compte AWS, en une commande.
#
#   ./scripts/bascule_aws.sh [AAAA-MM-JJ]
#
# Prerequis, une seule fois : dans le fichier .env a la racine du depot,
#   AWS_ACCESS_KEY_ID=...        (la cle creee dans la console IAM)
#   AWS_SECRET_ACCESS_KEY=...    (son secret, affiche une seule fois par AWS)
#   AWS_ENDPOINT_URL=            (VIDE : c'est cette ligne qui fait la bascule)
#
# Le script ne demande jamais de saisir une cle, ne l'affiche jamais et n'ecrit
# rien dans le depot. Il refuse de partir si quelque chose manque.

set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOUR="${1:-$(date -d yesterday +%F 2>/dev/null || date -v-1d +%F)}"
cd "$RACINE"

echo "== Radar entreprises Herault : bascule vers le vrai compte AWS =="
echo

if [ ! -f .env ]; then
  echo "ERREUR : pas de fichier .env. Copier .env.example en .env, puis y mettre la cle." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a

manquant=0
for variable in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY RADAR_BUCKET AWS_REGION; do
  if [ -z "${!variable:-}" ]; then
    echo "ERREUR : $variable est vide dans .env" >&2
    manquant=1
  fi
done
[ "$manquant" -eq 0 ] || exit 1

if [ -n "${AWS_ENDPOINT_URL:-}" ]; then
  echo "ERREUR : AWS_ENDPOINT_URL vaut « $AWS_ENDPOINT_URL »." >&2
  echo "        Pour parler au vrai AWS, cette ligne doit rester VIDE dans .env." >&2
  exit 1
fi

case "${AWS_ACCESS_KEY_ID}" in
  test|localstack|foo|"") echo "ERREUR : la cle ressemble a celle de LocalStack." >&2; exit 1 ;;
esac

echo "Compartiment : ${RADAR_BUCKET}"
echo "Region       : ${AWS_REGION}"
echo "Moteur       : ${RADAR_MOTEUR:-duckdb}"
echo "Parution     : ${JOUR}"
echo "Cle          : ${AWS_ACCESS_KEY_ID:0:4}**************** (jamais affichee en entier)"
echo

# 1. Verifier l'acces avant d'ecrire quoi que ce soit.
echo "-- 1/3 diagnostic d'acces"
docker compose run --rm \
  -e AWS_ENDPOINT_URL= \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_REGION -e AWS_DEFAULT_REGION="${AWS_REGION}" \
  -e RADAR_BUCKET -e RADAR_PREFIXE -e RADAR_MOTEUR \
  airflow-cli python -m radar.cli diagnostic

# 2. Rejouer exactement le meme graphe, sur le vrai S3.
echo
echo "-- 2/3 execution du graphe sur le vrai compte"
docker compose run --rm \
  -e AWS_ENDPOINT_URL= \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_REGION -e AWS_DEFAULT_REGION="${AWS_REGION}" \
  -e RADAR_BUCKET -e RADAR_PREFIXE -e RADAR_MOTEUR \
  airflow-cli airflow dags test radar_entreprises_herault "${JOUR}"

# 3. Exporter les chiffres depuis le vrai compte.
echo
echo "-- 3/3 export des chiffres"
AWS_ENDPOINT_URL= python -m uv run python scripts/export_results.py --jour "${JOUR}"

echo
echo "Termine. Les memes fichiers que sous LocalStack, cette fois dans s3://${RADAR_BUCKET}/${RADAR_PREFIXE}/."
