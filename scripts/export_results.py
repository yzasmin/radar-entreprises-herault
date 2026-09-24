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
    controles_silver = lire_json(cfg, cfg.chemin("qualite", f"date_parution={jour}", "controles_silver.json"))
    controles_gold = lire_json(cfg, cfg.chemin("qualite", f"date_parution={jour}", "controles_gold.json"))
    meta_bronze = lire_json(cfg, cfg.chemin("bronze", "bodacc", f"date_parution={jour}", "_meta.json"))

    # 1. Chiffres de tete.
    objets = lister(cfg, cfg.prefixe)
    volumetrie = {
        "jour": jour,
        "execution_emulee": bool(synthese.get("execution_emulee")),
        "moteur_requete": synthese.get("moteur_requete"),
        "nb_annonces_bronze": meta_bronze["nb_annonces"],
        "octets_bronze_json": meta_bronze["octets_bronze"],
        "nb_evenements_silver": len(evenements),
        "nb_lignes_gold": len(indicateurs),
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
    _ecrire_json("controles_qualite.json", {"silver": controles_silver, "gold": controles_gold})

    # 2. Tableau lisible des controles, celui qui est cite dans le README.
    lignes_controles = []
    for etape, rapport in (("silver", controles_silver), ("gold", controles_gold)):
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
            if "gold/indicateurs_commune_secteur/date_parution=" in o["cle"]
        }
    )
    toutes = []
    for partition in partitions:
        if partition >= debut_fenetre:
            toutes.extend(lire_indicateurs(cfg, partition))
    cumul = fenetre_glissante(toutes, jour, args.jours_fenetre)
    _ecrire_csv("indicateurs_commune_secteur.csv", indicateurs)
    _ecrire_csv("fenetre_glissante.csv", cumul)

    # Resume de la fenetre : c'est lui qui porte les chiffres de risque, une
    # journee seule pouvant ne contenir aucune procedure collective.
    colonnes = sorted({colonne for ligne in cumul for colonne in ligne if colonne.startswith("nb_")})
    resume = {
        "jour_fin": jour,
        "fenetre_jours": args.jours_fenetre,
        "nb_partitions": len([p for p in partitions if p >= debut_fenetre]),
        "partitions_retenues": [p for p in partitions if p >= debut_fenetre],
        "nb_communes": len({ligne["code_commune"] for ligne in cumul if ligne["code_commune"]}),
        "nb_couples_commune_secteur": len(cumul),
        "solde_net": sum(int(ligne.get("solde_net") or 0) for ligne in cumul),
        **{colonne: sum(int(ligne.get(colonne) or 0) for ligne in cumul) for colonne in colonnes},
    }
    _ecrire_json("resume_fenetre.json", resume)
    _ecrire_json(
        "partitions.json",
        {"jour": jour, "partitions_or": partitions, "fenetre_jours": args.jours_fenetre, "debut": debut_fenetre},
    )

    # 5. Cout S3 attendu. Tarifs publics AWS pour eu-north-1 (Stockholm), releves
    #    le 24/09/2026 dans l'index de tarification officiel :
    #      stockage Standard, 50 premiers To : 0,023 USD par Go et par mois
    #      requetes PUT, COPY, POST, LIST    : 0,005 USD pour 1 000
    #      requetes GET et autres            : 0,0004 USD pour 1 000
    #    Le graphe ecrit 8 objets par jour et en relit une quinzaine.
    go = volumetrie["octets_s3_total"] / 1_000_000_000
    objets_par_jour = 8
    lectures_par_jour = 20
    cout = {
        "region": cfg.region,
        "tarifs_usd": {"stockage_go_mois": 0.023, "put_1000": 0.005, "get_1000": 0.0004},
        "tarifs_releves_le": "2026-09-24",
        "octets_stockes": volumetrie["octets_s3_total"],
        "go_stockes": round(go, 6),
        "nb_objets": volumetrie["nb_objets_s3"],
        "cout_stockage_usd_par_mois": round(go * 0.023, 6),
        "cout_ecritures_usd_par_mois": round(objets_par_jour * 22 * 0.005 / 1000, 6),
        "cout_lectures_usd_par_mois": round(lectures_par_jour * 22 * 0.0004 / 1000, 6),
        "hypothese": "22 parutions par mois, 8 objets ecrits et 20 objets relus par parution",
        "parutions_par_mois": 22,
        "parutions_par_an": 250,
    }
    cout["cout_total_usd_par_mois"] = round(
        cout["cout_stockage_usd_par_mois"] + cout["cout_ecritures_usd_par_mois"] + cout["cout_lectures_usd_par_mois"], 6
    )
    # Projection a un an de collecte, au rythme mesure sur la fenetre traitee.
    octets_par_parution = volumetrie["octets_s3_total"] / max(len(partitions), 1)
    cout["octets_par_parution"] = round(octets_par_parution)
    cout["go_apres_un_an"] = round(octets_par_parution * cout["parutions_par_an"] / 1_000_000_000, 4)
    cout["cout_stockage_usd_mois_apres_un_an"] = round(cout["go_apres_un_an"] * 0.023, 4)
    _ecrire_json("cout_s3.json", cout)

    # 6. Lectures metier publiees : chaque phrase chiffree du README et de la fiche
    #    doit pouvoir se retrouver ici, y compris les rapports et les pourcentages.
    par_commune: dict[str, dict[str, int]] = {}
    par_secteur: dict[str, dict[str, int]] = {}
    for ligne in cumul:
        for index, cle in ((par_commune, ligne.get("nom_commune") or "commune inconnue"),
                           (par_secteur, ligne.get("libelle_section_naf") or "secteur inconnu")):
            panier = index.setdefault(cle, {})
            for colonne, valeur in ligne.items():
                if colonne.startswith("nb_") or colonne == "solde_net":
                    panier[colonne] = panier.get(colonne, 0) + int(valeur or 0)
    _ecrire_csv(
        "fenetre_par_commune.csv",
        [{"nom_commune": nom, **valeurs} for nom, valeurs in sorted(
            par_commune.items(), key=lambda c: -c[1].get("nb_defaillances", 0))],
    )
    _ecrire_csv(
        "fenetre_par_secteur.csv",
        [{"libelle_section_naf": nom, **valeurs} for nom, valeurs in sorted(
            par_secteur.items(), key=lambda c: -c[1].get("nb_defaillances", 0))],
    )

    defaillances_totales = resume.get("nb_defaillances", 0)
    premiere_commune = max(par_commune.items(), key=lambda c: c[1].get("nb_defaillances", 0))
    lectures = {
        "jour_fin": jour,
        "fenetre_jours": args.jours_fenetre,
        "commune_la_plus_touchee": premiere_commune[0],
        "defaillances_commune_la_plus_touchee": premiere_commune[1].get("nb_defaillances", 0),
        "creations_commune_la_plus_touchee": premiere_commune[1].get("nb_creations", 0),
        "defaillances_total": defaillances_totales,
        "part_defaillances_premiere_commune_pct": round(
            100 * premiere_commune[1].get("nb_defaillances", 0) / defaillances_totales, 1
        )
        if defaillances_totales
        else None,
        "secteurs_par_defaillances": [
            {
                "secteur": nom,
                "defaillances": valeurs.get("nb_defaillances", 0),
                "creations": valeurs.get("nb_creations", 0),
            }
            for nom, valeurs in sorted(par_secteur.items(), key=lambda c: -c[1].get("nb_defaillances", 0))[:5]
        ],
        "transferts_fenetre": resume.get("nb_transferts", 0),
        "creations_fenetre": resume.get("nb_creations", 0),
        "immatriculations_fenetre": resume.get("nb_immatriculations", 0),
        "surestimation_si_famille_brute_pct": round(
            100 * resume.get("nb_transferts", 0) / resume.get("nb_creations", 1), 1
        ),
    }
    _ecrire_json("lectures_metier.json", lectures)

    # 7. Evenements du jour, anonymises sur rien : ce sont des annonces legales publiques.
    _ecrire_csv("evenements_du_jour.csv", evenements)

    print(json.dumps(volumetrie, ensure_ascii=False, indent=2))
    print(json.dumps(resume, ensure_ascii=False, indent=2))
    print(json.dumps(cout, ensure_ascii=False, indent=2))
    print(json.dumps(lectures, ensure_ascii=False, indent=2))
    print(f"\n{len(list(SORTIE.glob('*')))} fichiers ecrits dans results/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
