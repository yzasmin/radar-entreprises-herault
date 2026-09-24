"""Les etapes du graphe, ecrites comme des fonctions ordinaires.

Airflow ne fait qu'appeler ces fonctions : aucune logique metier ne vit dans le
DAG. On peut donc rejouer tout le pipeline sans ordonnanceur
(`python -m radar.cli executer --jour 2026-09-22`), et tester chaque etape.
Chaque etape lit et ecrit sur S3 : les taches restent independantes, comme sur
un vrai ordonnanceur ou elles tournent dans des processus distincts.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa

from radar import extract, quality, transform
from radar.config import COUCHE_BRONZE, COUCHE_GOLD, COUCHE_SILVER, Config, charger_config
from radar.storage import assurer_seau, ecrire_json, ecrire_parquet, lire_json, lire_parquet

LOG = logging.getLogger(__name__)

# `date_parution` n'est PAS dans les fichiers : c'est la cle de partition, portee par
# le chemin `date_parution=YYYY-MM-DD/`. Athena l'exige, DuckDB la reconstruit avec
# `hive_partitioning = true`, et la colonne n'est stockee qu'une fois au lieu de N.
SCHEMA_SILVER = pa.schema(
    [
        ("id_annonce", pa.string()),
        ("type_evenement", pa.string()),
        ("famille_bodacc", pa.string()),
        ("gravite", pa.int32()),
        ("siren", pa.string()),
        ("denomination", pa.string()),
        ("activite_declaree", pa.string()),
        ("code_commune", pa.string()),
        ("nom_commune", pa.string()),
        ("code_postal", pa.string()),
        ("population_commune", pa.int64()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("tribunal", pa.string()),
        ("date_jugement", pa.string()),
        ("montant_vente_eur", pa.float64()),
        ("section_naf", pa.string()),
        ("libelle_section_naf", pa.string()),
        ("code_naf", pa.string()),
        ("tranche_effectif", pa.string()),
        ("date_creation_entreprise", pa.string()),
        ("url_annonce", pa.string()),
    ]
)

SCHEMA_GOLD = pa.schema(
    [
        ("code_commune", pa.string()),
        ("nom_commune", pa.string()),
        ("population_commune", pa.int64()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("section_naf", pa.string()),
        ("libelle_section_naf", pa.string()),
        ("nb_creations", pa.int32()),
        ("nb_immatriculations", pa.int32()),
        ("nb_transferts", pa.int32()),
        ("nb_ventes_fonds", pa.int32()),
        ("nb_modifications", pa.int32()),
        ("nb_radiations", pa.int32()),
        ("nb_sauvegardes", pa.int32()),
        ("nb_redressements", pa.int32()),
        ("nb_liquidations", pa.int32()),
        ("nb_plans_cession", pa.int32()),
        ("nb_defaillances", pa.int32()),
        ("nb_depots_comptes", pa.int32()),
        ("nb_evenements", pa.int32()),
        ("solde_net", pa.int32()),
        ("montant_ventes_eur", pa.float64()),
    ]
)


def _table(lignes: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    """Table Arrow au schema fige : un champ absent devient null, jamais une colonne surprise."""
    colonnes = {
        champ.name: pa.array([ligne.get(champ.name) for ligne in lignes], type=champ.type) for champ in schema
    }
    return pa.Table.from_pydict(colonnes, schema=schema)


def _partition(couche: str, table: str, jour: str, fichier: str, cfg: Config) -> str:
    return cfg.chemin(couche, table, f"date_parution={jour}", fichier)


def _avec_partition(lignes: list[dict[str, Any]], jour: str) -> list[dict[str, Any]]:
    """Relit la cle de partition depuis le chemin, comme le ferait Athena."""
    for ligne in lignes:
        ligne["date_parution"] = jour
    return lignes


def lire_evenements(cfg: Config, jour: str) -> list[dict[str, Any]]:
    table = lire_parquet(cfg, _partition(COUCHE_SILVER, "evenements", jour, "evenements.parquet", cfg))
    return _avec_partition(table.to_pylist(), jour)


def lire_indicateurs(cfg: Config, jour: str) -> list[dict[str, Any]]:
    table = lire_parquet(cfg, _partition(COUCHE_GOLD, "indicateurs_commune_secteur", jour, "indicateurs.parquet", cfg))
    return _avec_partition(table.to_pylist(), jour)


# ---------------------------------------------------------------------------
# Etapes
# ---------------------------------------------------------------------------


def etape_preparer(jour: str, cfg: Config | None = None) -> dict[str, Any]:
    """Verifie la cible S3 et rafraichit le referentiel des communes."""
    cfg = cfg or charger_config()
    cree = assurer_seau(cfg)
    communes = extract.communes_du_departement(cfg)
    if not communes:
        raise extract.ExtractionError("referentiel geographique vide")
    cle = cfg.chemin(COUCHE_BRONZE, "referentiel", f"communes_{cfg.departement}.json")
    ecrire_json(cfg, communes, cle)
    rapport = {
        "jour": jour,
        "endpoint": cfg.endpoint_url or f"https://s3.{cfg.region}.amazonaws.com",
        "execution_emulee": cfg.est_emule,
        "bucket": cfg.bucket,
        "seau_cree_par_le_graphe": cree,
        "nb_communes_referentiel": len(communes),
        "departement": cfg.departement,
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return rapport


def etape_extraire(jour: str, cfg: Config | None = None) -> dict[str, Any]:
    """Bronze : les annonces du jour, telles que la source les rend."""
    cfg = cfg or charger_config()
    annonces = extract.extraire_bodacc(cfg, jour)
    cle = _partition(COUCHE_BRONZE, "bodacc", jour, "annonces.json", cfg)
    taille = ecrire_json(cfg, annonces, cle)
    derniere = extract.derniere_parution_disponible(cfg)
    historique = extract.historique_volumetrie(cfg, jour, jours=90)
    meta = {
        "jour": jour,
        "nb_annonces": len(annonces),
        "octets_bronze": taille,
        "cle_bronze": cle,
        "derniere_parution_source": derniere,
        "historique_volumetrie": historique,
        "extrait_le": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    ecrire_json(cfg, meta, cfg.chemin(COUCHE_BRONZE, "bodacc", f"date_parution={jour}", "_meta.json"))
    print(json.dumps({k: v for k, v in meta.items() if k != "historique_volumetrie"}, ensure_ascii=False, indent=2))
    return meta


def etape_enrichir(jour: str, cfg: Config | None = None) -> dict[str, Any]:
    """Bronze : fiche SIRENE de chaque entreprise citee, via l'API Recherche d'entreprises."""
    cfg = cfg or charger_config()
    annonces = lire_json(cfg, _partition(COUCHE_BRONZE, "bodacc", jour, "annonces.json", cfg))
    sirens: list[str] = []
    for annonce in annonces:
        for siren in transform.extraire_sirens(annonce.get("registre")):
            if siren not in sirens:
                sirens.append(siren)
    fiches = extract.enrichir_sirens(cfg, sirens) if cfg.enrichissement_actif else {}
    cle = _partition(COUCHE_BRONZE, "entreprises", jour, "fiches.json", cfg)
    ecrire_json(cfg, fiches, cle)
    rapport = {
        "jour": jour,
        "nb_sirens_distincts": len(sirens),
        "nb_sirens_demandes": min(len(sirens), cfg.max_sirens_enrichis),
        "nb_fiches_obtenues": len(fiches),
        "enrichissement_actif": cfg.enrichissement_actif,
        "taux_couverture": round(len(fiches) / len(sirens), 4) if sirens else None,
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return rapport


def etape_silver(jour: str, cfg: Config | None = None) -> dict[str, Any]:
    """Silver : un evenement type par annonce, rattache a une commune et a un secteur."""
    cfg = cfg or charger_config()
    annonces = lire_json(cfg, _partition(COUCHE_BRONZE, "bodacc", jour, "annonces.json", cfg))
    communes = lire_json(cfg, cfg.chemin(COUCHE_BRONZE, "referentiel", f"communes_{cfg.departement}.json"))
    try:
        fiches = lire_json(cfg, _partition(COUCHE_BRONZE, "entreprises", jour, "fiches.json", cfg))
    except Exception:
        LOG.warning("aucune fiche d'enrichissement pour %s, on continue sans NAF", jour)
        fiches = {}
    index = transform.indexer_communes(communes)
    evenements = transform.construire_argent(annonces, index, fiches)
    cle = _partition(COUCHE_SILVER, "evenements", jour, "evenements.parquet", cfg)
    taille = ecrire_parquet(cfg, _table(evenements, SCHEMA_SILVER), cle)
    rapport = {
        "jour": jour,
        "nb_annonces_bronze": len(annonces),
        "nb_evenements_silver": len(evenements),
        "nb_doublons_ecartes": len(annonces) - len(evenements),
        "octets_parquet": taille,
        "cle_silver": cle,
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return rapport


def etape_controler_silver(jour: str, cfg: Config | None = None) -> dict[str, Any]:
    """Controles bloquants sur la couche silver."""
    cfg = cfg or charger_config()
    evenements = lire_evenements(cfg, jour)
    meta = lire_json(cfg, cfg.chemin(COUCHE_BRONZE, "bodacc", f"date_parution={jour}", "_meta.json"))
    resultats = quality.controler_argent(
        evenements,
        jour=jour,
        departement=cfg.departement,
        historique=meta.get("historique_volumetrie", []),
        derniere_parution=meta.get("derniere_parution_source"),
    )
    rapport = quality.exiger(resultats, "couche silver")
    ecrire_json(cfg, rapport, cfg.chemin("qualite", f"date_parution={jour}", "controles_silver.json"))
    return rapport


def etape_gold(jour: str, cfg: Config | None = None) -> dict[str, Any]:
    """Gold : indicateurs par commune et section NAF, plus la liste des signaux."""
    cfg = cfg or charger_config()
    evenements = lire_evenements(cfg, jour)
    lignes_gold = transform.agreger_par_commune_secteur(evenements)
    taille = ecrire_parquet(
        cfg,
        _table(lignes_gold, SCHEMA_GOLD),
        _partition(COUCHE_GOLD, "indicateurs_commune_secteur", jour, "indicateurs.parquet", cfg),
    )
    signaux = transform.signaux_prioritaires(evenements)
    ecrire_json(cfg, signaux, cfg.chemin(COUCHE_GOLD, "signaux", f"date_parution={jour}", "signaux.json"))
    rapport = {
        "jour": jour,
        "nb_lignes_gold": len(lignes_gold),
        "nb_signaux": len(signaux),
        "octets_parquet": taille,
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return rapport


def etape_controler_gold(jour: str, cfg: Config | None = None) -> dict[str, Any]:
    """Controles bloquants sur la couche gold."""
    cfg = cfg or charger_config()
    evenements = lire_evenements(cfg, jour)
    lignes_gold = lire_indicateurs(cfg, jour)
    resultats = quality.controler_or(evenements, lignes_gold)
    rapport = quality.exiger(resultats, "couche gold")
    ecrire_json(cfg, rapport, cfg.chemin("qualite", f"date_parution={jour}", "controles_gold.json"))
    return rapport


def etape_publier(jour: str, cfg: Config | None = None) -> dict[str, Any]:
    """Publication : synthese du jour, interrogee via l'entrepot (DuckDB ou Athena)."""
    cfg = cfg or charger_config()
    from radar.warehouse import charger_requetes, interroger

    evenements = lire_evenements(cfg, jour)
    lignes_gold = lire_indicateurs(cfg, jour)
    synthese = transform.synthese_journaliere(evenements, lignes_gold)
    synthese["jour"] = jour
    synthese["execution_emulee"] = cfg.est_emule
    synthese["moteur_requete"] = cfg.moteur_requete

    requetes = charger_requetes()
    interrogations: dict[str, Any] = {}
    for nom in ("top_communes", "par_section", "signaux_du_jour", "solde_net_departement"):
        if nom not in requetes:
            continue
        interrogations[nom] = interroger(cfg, requetes[nom], {"jour": jour})
    synthese["requetes_entrepot"] = {nom: len(lignes) for nom, lignes in interrogations.items()}

    ecrire_json(cfg, synthese, cfg.chemin("publication", f"date_parution={jour}", "synthese.json"))
    for nom, lignes in interrogations.items():
        ecrire_json(cfg, lignes, cfg.chemin("publication", f"date_parution={jour}", f"{nom}.json"))
    print(json.dumps(synthese, ensure_ascii=False, indent=2, default=str))
    return synthese


ETAPES = {
    "preparer": etape_preparer,
    "extraire": etape_extraire,
    "enrichir": etape_enrichir,
    "silver": etape_silver,
    "controler_silver": etape_controler_silver,
    "gold": etape_gold,
    "controler_gold": etape_controler_gold,
    "publier": etape_publier,
}

ORDRE = ("preparer", "extraire", "enrichir", "silver", "controler_silver", "gold", "controler_gold", "publier")
