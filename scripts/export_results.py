"""Exporte dans `results/` tout ce que la fiche et le README publient.

S'execute apres le graphe, contre le meme S3 (LocalStack ou AWS reel). Chaque
chiffre publie doit se retrouver dans un fichier de ce dossier : c'est la regle
du portfolio, et c'est ce script qui la rend tenable.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radar.config import charger_config
from radar.pipeline import lire_evenements, lire_indicateurs
from radar.storage import lire_json, lister
from radar.warehouse import charger_requetes, interroger

RACINE = Path(__file__).resolve().parents[1]
SORTIE = RACINE / "results"


def _ecrire_json(nom: str, contenu) -> Path:
    chemin = SORTIE / nom
    chemin.write_text(json.dumps(contenu, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return chemin


def _ecrire_csv(nom: str, lignes: list[dict]) -> Path | None:
    if not lignes:
        return None
    chemin = SORTIE / nom
    with chemin.open("w", encoding="utf-8", newline="") as fichier:
        auteur = csv.DictWriter(fichier, fieldnames=list(lignes[0].keys()))
        auteur.writeheader()
        auteur.writerows(lignes)
    return chemin


def main() -> int:
    parseur = argparse.ArgumentParser(description="Exporte les chiffres publies")
    parseur.add_argument("--jour", required=True, help="date de parution traitee, AAAA-MM-JJ")
    parseur.add_argument("--jours-fenetre", type=int, default=30)
    args = parseur.parse_args()
    SORTIE.mkdir(exist_ok=True)
    cfg = charger_config()
    jour = args.jour

    evenements = lire_evenements(cfg, jour)
    indicateurs = lire_indicateurs(cfg, jour)
    synthese = lire_json(cfg, cfg.chemin("publication", f"date_parution={jour}", "synthese.json"))
    controles_argent = lire_json(cfg, cfg.chemin("qualite", f"date_parution={jour}", "controles_argent.json"))
    controles_or = lire_json(cfg, cfg.chemin("qualite", f"date_parution={jour}", "controles_or.json"))
    meta_bronze = lire_json(cfg, cfg.chemin("bronze", "bodacc", f"date_parution={jour}", "_meta.json"))

    # 1. Chiffres de tete.
    objets = lister(cfg, cfg.prefixe)
    volumetrie = {
        "jour": jour,
        "execution_emulee": bool(synthese.get("execution_emulee")),
        "moteur_requete": synthese.get("moteur_requete"),
        "nb_annonces_bronze": meta_bronze["nb_annonces"],
        "octets_bronze_json": meta_bronze["octets_bronze"],
        "nb_evenements_argent": len(evenements),
        "nb_lignes_or": len(indicateurs),
        "nb_objets_s3": len(objets),
        "octets_s3_total": sum(o["taille"] for o in objets),
        "octets_parquet_argent": next(
            (o["taille"] for o in objets if o["cle"].endswith(f"date_parution={jour}/evenements.parquet")), None
        ),
        "octets_parquet_or": next(
            (o["taille"] for o in objets if o["cle"].endswith(f"date_parution={jour}/indicateurs.parquet")), None
        ),
        "derniere_parution_source": meta_bronze["derniere_parution_source"],
        "extrait_le": meta_bronze["extrait_le"],
    }
    volumetrie["compression_parquet_vs_json"] = (
        round(meta_bronze["octets_bronze"] / volumetrie["octets_parquet_argent"], 2)
        if volumetrie["octets_parquet_argent"]
        else None
    )
    _ecrire_json("volumetrie.json", volumetrie)
    _ecrire_json("synthese.json", synthese)
    _ecrire_json("controles_qualite.json", {"argent": controles_argent, "or": controles_or})

    # 2. Tableau lisible des controles, celui qui est cite dans le README.
    lignes_controles = []
    for etape, rapport in (("argent", controles_argent), ("or", controles_or)):
        for controle in rapport["controles"]:
            lignes_controles.append(
                {
                    "etape": etape,
                    "controle": controle["nom"],
                    "statut": controle["statut"],
                    "bloquant": controle["bloquant"],
                    "attendu": controle["attendu"],
                    "observe": controle["observe"],
                }
            )
    _ecrire_csv("controles_qualite.csv", lignes_controles)

    # 3. Sorties de l'entrepot, produites par le meme SQL que sur Athena.
    requetes = charger_requetes()
    chronos = {}
    for nom, sql in requetes.items():
        debut = time.perf_counter()
        lignes = interroger(cfg, sql, {"jour": jour})
        chronos[nom] = {"lignes": len(lignes), "duree_s": round(time.perf_counter() - debut, 3)}
        _ecrire_csv(f"requete_{nom}.csv", lignes)
    _ecrire_json("requetes_entrepot.json", {"moteur": cfg.moteur_requete, "jour": jour, "requetes": chronos})

    # 4. Serie sur la fenetre glissante, pour le graphique et la lecture metier.
    #    On agrege les partitions deja presentes dans le seau.
    from radar.transform import fenetre_glissante

    debut_fenetre = (date.fromisoformat(jour) - timedelta(days=args.jours_fenetre - 1)).isoformat()
    partitions = sorted(
        {
            o["cle"].split("date_parution=")[1].split("/")[0]
            for o in objets
            if "or/indicateurs_commune_secteur/date_parution=" in o["cle"]
        }
    )
    toutes = []
    for partition in partitions:
        if partition >= debut_fenetre:
            toutes.extend(lire_indicateurs(cfg, partition))
    _ecrire_csv("indicateurs_commune_secteur.csv", indicateurs)
    _ecrire_csv("fenetre_glissante.csv", fenetre_glissante(toutes, jour, args.jours_fenetre))
    _ecrire_json(
        "partitions.json",
        {"jour": jour, "partitions_or": partitions, "fenetre_jours": args.jours_fenetre, "debut": debut_fenetre},
    )

    # 5. Evenements du jour, anonymises sur rien : ce sont des annonces legales publiques.
    _ecrire_csv("evenements_du_jour.csv", evenements)

    print(json.dumps(volumetrie, ensure_ascii=False, indent=2))
    print(f"\n{len(list(SORTIE.glob('*')))} fichiers ecrits dans results/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
