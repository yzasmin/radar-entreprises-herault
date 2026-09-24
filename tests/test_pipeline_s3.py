"""Tests du pipeline de bout en bout, sur un S3 simule en memoire (moto).

Ces tests couvrent le vrai chemin boto3 : ecriture Parquet, partitionnement
Hive, relecture, controles bloquants. Aucun appel reseau : les trois fonctions
d'extraction sont remplacees par les jeux d'essai.
"""

from __future__ import annotations

import pytest

moto = pytest.importorskip("moto")

# Historiques d'essai : une dizaine de jours suffisent pour que le controle de
# volumetrie se calibre sur des centiles plutot que sur son repli.
HISTORIQUE_CREUX = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 20]
HISTORIQUE_FOURNI = [200, 300, 400, 420, 440, 460, 480, 500, 520, 600, 700, 800]
from moto import mock_aws

from radar import extract, pipeline, quality
from radar.config import Config
from radar.storage import client_s3, lire_json, lister


@pytest.fixture
def cfg(monkeypatch):
    for nom, valeur in {
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_SESSION_TOKEN": "test",
        "AWS_REGION": "eu-north-1",
        "RADAR_BUCKET": "radar-test",
        "RADAR_PREFIXE": "radar",
        "RADAR_DEPARTEMENT": "34",
    }.items():
        monkeypatch.setenv(nom, valeur)
    # Pas d'endpoint : moto intercepte les appels boto3 au niveau du client.
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    return Config()


@pytest.fixture
def sources(monkeypatch, annonces, communes, fiches):
    monkeypatch.setattr(extract, "extraire_bodacc", lambda cfg, jour, departement=None: annonces)
    monkeypatch.setattr(extract, "compter_bodacc", lambda cfg, jour, departement=None: len(annonces))
    monkeypatch.setattr(extract, "communes_du_departement", lambda cfg, departement=None: communes)
    monkeypatch.setattr(extract, "derniere_parution_disponible", lambda cfg, departement=None: "2026-09-23")
    monkeypatch.setattr(
        extract, "historique_volumetrie", lambda cfg, jour, jours=60, departement=None: HISTORIQUE_CREUX
    )
    monkeypatch.setattr(extract, "enrichir_sirens", lambda cfg, sirens: fiches)


def _dérouler(cfg, jour, jusqu_a="controler_gold"):
    rapports = {}
    for nom in pipeline.ORDRE:
        rapports[nom] = pipeline.ETAPES[nom](jour, cfg)
        if nom == jusqu_a:
            break
    return rapports


@mock_aws
def test_graphe_complet_jusqu_a_la_couche_or(cfg, sources, jour):
    rapports = _dérouler(cfg, jour)
    assert rapports["preparer"]["nb_communes_referentiel"] == 7
    assert rapports["extraire"]["nb_annonces"] == 10
    assert rapports["silver"]["nb_evenements_silver"] == 9
    assert rapports["silver"]["nb_doublons_ecartes"] == 1
    assert rapports["gold"]["nb_signaux"] == 2
    assert rapports["controler_gold"]["echecs_bloquants"] == 0


@mock_aws
def test_arborescence_s3_partitionnee(cfg, sources, jour):
    _dérouler(cfg, jour)
    cles = [objet["cle"] for objet in lister(cfg, "radar/")]
    assert f"radar/silver/evenements/date_parution={jour}/evenements.parquet" in cles
    assert f"radar/gold/indicateurs_commune_secteur/date_parution={jour}/indicateurs.parquet" in cles
    assert f"radar/qualite/date_parution={jour}/controles_silver.json" in cles


@mock_aws
def test_le_parquet_ne_contient_pas_la_cle_de_partition(cfg, sources, jour):
    """Athena refuse une colonne qui est aussi une cle de partition."""
    from radar.storage import lire_parquet

    _dérouler(cfg, jour)
    table = lire_parquet(cfg, f"radar/silver/evenements/date_parution={jour}/evenements.parquet")
    assert "date_parution" not in table.column_names
    assert table.num_rows == 9


@mock_aws
def test_les_controles_silver_sont_enregistres(cfg, sources, jour):
    _dérouler(cfg, jour, jusqu_a="controler_silver")
    rapport = lire_json(cfg, f"radar/qualite/date_parution={jour}/controles_silver.json")
    noms = {c["nom"] for c in rapport["controles"]}
    assert {"unicite_id_annonce", "fraicheur_source", "volumetrie_dans_la_norme"} <= noms
    assert rapport["echecs_bloquants"] == 0


@mock_aws
def test_un_effondrement_de_volumetrie_arrete_le_graphe(cfg, monkeypatch, annonces, communes, fiches, jour):
    monkeypatch.setattr(extract, "communes_du_departement", lambda cfg, departement=None: communes)
    monkeypatch.setattr(extract, "extraire_bodacc", lambda cfg, jour, departement=None: annonces[:1])
    monkeypatch.setattr(extract, "compter_bodacc", lambda cfg, jour, departement=None: len(annonces[:1]))
    monkeypatch.setattr(extract, "derniere_parution_disponible", lambda cfg, departement=None: "2026-09-23")
    monkeypatch.setattr(
        extract, "historique_volumetrie", lambda cfg, jour, jours=60, departement=None: HISTORIQUE_FOURNI
    )
    monkeypatch.setattr(extract, "enrichir_sirens", lambda cfg, sirens: fiches)
    pipeline.etape_preparer(jour, cfg)
    pipeline.etape_extraire(jour, cfg)
    pipeline.etape_enrichir(jour, cfg)
    pipeline.etape_silver(jour, cfg)
    with pytest.raises(quality.QualiteError, match="volumetrie"):
        pipeline.etape_controler_silver(jour, cfg)


@mock_aws
def test_une_source_gelee_arrete_le_graphe(cfg, monkeypatch, annonces, communes, fiches, jour):
    monkeypatch.setattr(extract, "communes_du_departement", lambda cfg, departement=None: communes)
    monkeypatch.setattr(extract, "extraire_bodacc", lambda cfg, jour, departement=None: annonces)
    monkeypatch.setattr(extract, "compter_bodacc", lambda cfg, jour, departement=None: len(annonces))
    monkeypatch.setattr(extract, "derniere_parution_disponible", lambda cfg, departement=None: "2026-06-01")
    monkeypatch.setattr(
        extract, "historique_volumetrie", lambda cfg, jour, jours=60, departement=None: HISTORIQUE_CREUX
    )
    monkeypatch.setattr(extract, "enrichir_sirens", lambda cfg, sirens: fiches)
    for nom in ("preparer", "extraire", "enrichir", "silver"):
        pipeline.ETAPES[nom](jour, cfg)
    with pytest.raises(quality.QualiteError, match="fraicheur_source"):
        pipeline.etape_controler_silver(jour, cfg)


@mock_aws
def test_la_couche_silver_survit_a_une_panne_d_enrichissement(cfg, monkeypatch, annonces, communes, jour):
    """L'API Recherche d'entreprises est un tiers : sa panne degrade, elle n'arrete pas."""
    monkeypatch.setattr(extract, "communes_du_departement", lambda cfg, departement=None: communes)
    monkeypatch.setattr(extract, "extraire_bodacc", lambda cfg, jour, departement=None: annonces)
    monkeypatch.setattr(extract, "compter_bodacc", lambda cfg, jour, departement=None: len(annonces))
    monkeypatch.setattr(extract, "derniere_parution_disponible", lambda cfg, departement=None: "2026-09-23")
    monkeypatch.setattr(
        extract, "historique_volumetrie", lambda cfg, jour, jours=60, departement=None: HISTORIQUE_CREUX
    )

    def panne(cfg, sirens):
        raise extract.ExtractionError("API indisponible")

    monkeypatch.setattr(extract, "enrichir_sirens", panne)
    pipeline.etape_preparer(jour, cfg)
    pipeline.etape_extraire(jour, cfg)
    with pytest.raises(extract.ExtractionError):
        pipeline.etape_enrichir(jour, cfg)
    # La couche silver se construit quand meme, sans NAF.
    rapport = pipeline.etape_silver(jour, cfg)
    assert rapport["nb_evenements_silver"] == 9
    resultats = pipeline.etape_gold(jour, cfg)
    assert resultats["nb_lignes_gold"] >= 1


@mock_aws
def test_rejeu_idempotent(cfg, sources, jour):
    """Rejouer le meme jour ecrase la partition, il ne l'empile pas."""
    _dérouler(cfg, jour)
    premier = [o for o in lister(cfg, "radar/silver/")]
    _dérouler(cfg, jour)
    second = [o for o in lister(cfg, "radar/silver/")]
    assert len(premier) == len(second) == 1


@mock_aws
def test_le_seau_est_cree_une_seule_fois(cfg, sources, jour):
    assert pipeline.etape_preparer(jour, cfg)["seau_cree_par_le_graphe"] is True
    assert pipeline.etape_preparer(jour, cfg)["seau_cree_par_le_graphe"] is False
    seaux = client_s3(cfg).list_buckets()["Buckets"]
    assert [b["Name"] for b in seaux] == ["radar-test"]
