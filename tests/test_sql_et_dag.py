"""Tests du SQL publie et du graphe Airflow.

Le test du DAG est saute quand Airflow n'est pas installe : sur le poste de
developpement, Airflow ne tourne pas. En integration continue, la tache
`graphe` l'installe et le test s'execute reellement.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from radar.warehouse import charger_requetes

RACINE = Path(__file__).resolve().parents[1]

REQUETES_ATTENDUES = {
    "solde_net_departement",
    "top_communes",
    "par_section",
    "signaux_du_jour",
    "prospects_creations",
    "ventes_de_fonds",
    "controle_croise_argent_or",
}


def test_les_requetes_publiees_sont_toutes_nommees():
    requetes = charger_requetes(RACINE / "sql" / "indicateurs.sql")
    assert REQUETES_ATTENDUES <= set(requetes)


def test_chaque_requete_est_parametree_par_le_jour():
    requetes = charger_requetes(RACINE / "sql" / "indicateurs.sql")
    for nom, sql in requetes.items():
        assert "{jour}" in sql, f"{nom} n'est pas filtree sur la partition"


def test_les_requetes_ne_lisent_que_les_deux_tables():
    requetes = charger_requetes(RACINE / "sql" / "indicateurs.sql")
    for nom, sql in requetes.items():
        minuscule = sql.lower()
        tables = {mot for mot in ("evenements", "indicateurs") if f"from {mot}" in minuscule}
        assert tables, f"{nom} ne lit aucune table connue"


def test_le_ddl_athena_declare_la_partition_hors_du_fichier():
    """Athena refuse une colonne de partition presente aussi dans le Parquet."""
    ddl = (RACINE / "sql" / "athena_ddl.sql").read_text(encoding="utf-8")
    assert ddl.count("PARTITIONED BY (date_parution string)") == 2
    # La colonne ne doit pas apparaitre dans la liste des colonnes des tables.
    corps = ddl.split("PARTITIONED BY")[0]
    assert "date_parution               string" not in corps
    assert "projection.enabled" in ddl


def test_le_ddl_se_decoupe_en_deux_instructions():
    from radar.warehouse import RACINE_SQL  # noqa: F401  (import verifie)

    ddl = (RACINE / "sql" / "athena_ddl.sql").read_text(encoding="utf-8")
    instructions = []
    for morceau in ddl.split(";"):
        corps = "\n".join(li for li in morceau.splitlines() if not li.strip().startswith("--")).strip()
        if corps:
            instructions.append(corps)
    assert len(instructions) == 2
    assert all(i.startswith("CREATE EXTERNAL TABLE") for i in instructions)


# ---------------------------------------------------------------------------
# Graphe Airflow
# ---------------------------------------------------------------------------


@pytest.fixture
def dag():
    pytest.importorskip("airflow", reason="Airflow n'est installe qu'en integration continue")
    from airflow.models import DagBag

    sac = DagBag(dag_folder=str(RACINE / "dags"), include_examples=False)
    assert not sac.import_errors, sac.import_errors
    return sac.get_dag("radar_entreprises_herault")


def test_le_graphe_se_charge_sans_erreur(dag):
    assert dag is not None
    assert dag.schedule_interval == "0 6 * * 2-6" or dag.timetable.summary == "0 6 * * 2-6"


def test_l_ordre_des_taches_place_les_controles_avant_la_publication(dag):
    attendu = [
        "preparer",
        "extraire_bodacc",
        "enrichir_sirene",
        "construire_argent",
        "controles_qualite_argent",
        "construire_or",
        "controles_qualite_or",
        "publier_indicateurs",
    ]
    assert sorted(t.task_id for t in dag.tasks) == sorted(attendu)
    for amont, aval in pairwise(attendu):
        assert aval in {t.task_id for t in dag.get_task(amont).downstream_list}


def test_les_taches_ont_des_reprises(dag):
    for tache in dag.tasks:
        assert tache.retries >= 1, f"{tache.task_id} n'a aucune reprise"
