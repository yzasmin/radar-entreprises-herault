"""Ligne de commande : rejouer une etape ou tout le graphe sans Airflow.

Utile pour deverminer une etape isolee, et pour executer le pipeline sur un
poste qui n'a pas de moteur Docker. L'ordonnanceur n'apporte rien a la logique,
il apporte la planification, les reprises et l'observabilite.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta

from radar.config import charger_config
from radar.pipeline import ETAPES, ORDRE


def _jour_par_defaut() -> str:
    """Le BODACC publie du mardi au samedi : la veille est le choix sur."""
    return (date.today() - timedelta(days=1)).isoformat()


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(prog="radar", description="Radar economique de l'Herault")
    sous = parseur.add_subparsers(dest="commande", required=True)

    executer = sous.add_parser("executer", help="rejoue tout le graphe pour un jour")
    executer.add_argument("--jour", default=_jour_par_defaut(), help="date de parution AAAA-MM-JJ")

    etape = sous.add_parser("etape", help="rejoue une seule etape")
    etape.add_argument("nom", choices=sorted(ETAPES))
    etape.add_argument("--jour", default=_jour_par_defaut())

    sous.add_parser("diagnostic", help="affiche la cible S3 et le moteur de requete")
    sous.add_parser("catalogue", help="declare les tables Athena (compte AWS reel requis)")

    requete = sous.add_parser("requete", help="execute une requete nommee de sql/indicateurs.sql")
    requete.add_argument("nom")
    requete.add_argument("--jour", default=_jour_par_defaut())
    return parseur


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = construire_parseur().parse_args(argv)
    cfg = charger_config()

    if args.commande == "diagnostic":
        from radar.warehouse import verifier_acces_s3

        print(json.dumps(verifier_acces_s3(cfg), ensure_ascii=False, indent=2))
        return 0

    if args.commande == "catalogue":
        from radar.warehouse import declarer_tables_athena

        print(json.dumps(declarer_tables_athena(cfg), ensure_ascii=False, indent=2))
        return 0

    if args.commande == "requete":
        from radar.warehouse import charger_requetes, interroger

        requetes = charger_requetes()
        if args.nom not in requetes:
            print(f"requete inconnue. Disponibles : {', '.join(sorted(requetes))}", file=sys.stderr)
            return 2
        lignes = interroger(cfg, requetes[args.nom], {"jour": args.jour})
        print(json.dumps(lignes, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.commande == "etape":
        ETAPES[args.nom](args.jour, cfg)
        return 0

    for nom in ORDRE:
        print(f"\n===== {nom} ({args.jour}) =====")
        ETAPES[nom](args.jour, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
