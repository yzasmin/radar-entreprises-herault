"""Entrepot : les memes Parquet interroges par Athena ou par DuckDB.

Le projet doit rester executable sans compte AWS. Les deux moteurs lisent
exactement les memes fichiers Parquet partitionnes, avec le meme SQL : Athena
quand un compte est disponible, DuckDB sinon. C'est le moteur qui change, pas
les donnees ni les requetes.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from radar.config import Config
from radar.storage import client_athena, client_s3

LOG = logging.getLogger(__name__)

# Le dossier sql/ voyage avec le depot. Dans le conteneur Airflow le depot est
# monte ailleurs : RADAR_SQL_DIR permet de le dire sans toucher au code.
RACINE_SQL = Path(os.environ.get("RADAR_SQL_DIR") or (Path(__file__).resolve().parents[2] / "sql"))


def charger_requetes(chemin: Path | None = None) -> dict[str, str]:
    """Lit `sql/indicateurs.sql` et decoupe sur les marqueurs `-- nom: <cle>`."""
    fichier = chemin or (RACINE_SQL / "indicateurs.sql")
    requetes: dict[str, str] = {}
    nom_courant: str | None = None
    tampon: list[str] = []
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        if ligne.strip().startswith("-- nom:"):
            if nom_courant:
                requetes[nom_courant] = "\n".join(tampon).strip()
            nom_courant = ligne.split(":", 1)[1].strip()
            tampon = []
        elif nom_courant is not None:
            tampon.append(ligne)
    if nom_courant:
        requetes[nom_courant] = "\n".join(tampon).strip()
    return requetes


# ---------------------------------------------------------------------------
# DuckDB
# ---------------------------------------------------------------------------


def connexion_duckdb(cfg: Config):
    """Connexion DuckDB configuree pour lire s3:// (LocalStack ou AWS reel)."""
    import duckdb

    connexion = duckdb.connect()
    connexion.execute("INSTALL httpfs; LOAD httpfs;")
    cle = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if not cfg.est_emule and cle and secret:
        # Vrai AWS, avec la cle deja presente dans l'environnement : on la passe
        # telle quelle, sans dependre de l'extension aws ni d'un profil local.
        connexion.execute(
            f"""
            CREATE OR REPLACE SECRET radar (
                TYPE S3, KEY_ID '{cle}', SECRET '{secret}', REGION '{cfg.region}'
            );
            """
        )
        return connexion
    if cfg.est_emule:
        cle = cle or "test"
        secret = secret or "test"
        hote = cfg.endpoint_url.replace("http://", "").replace("https://", "").rstrip("/")
        connexion.execute(
            f"""
            CREATE OR REPLACE SECRET radar (
                TYPE S3, KEY_ID '{cle}', SECRET '{secret}', REGION '{cfg.region}',
                ENDPOINT '{hote}', URL_STYLE 'path', USE_SSL false
            );
            """
        )
    else:
        # Ni endpoint emule, ni cle dans l'environnement : on laisse DuckDB
        # parcourir la chaine d'identifiants (profil, role, variables).
        connexion.execute("INSTALL aws; LOAD aws;")
        connexion.execute(
            f"""
            CREATE OR REPLACE SECRET radar (
                TYPE S3, PROVIDER credential_chain, REGION '{cfg.region}'
            );
            """
        )
    return connexion


def _vues_duckdb(cfg: Config) -> str:
    """Vues nommees comme les tables du catalogue Glue, pour un SQL identique."""
    return f"""
    CREATE OR REPLACE VIEW evenements AS
      SELECT * FROM read_parquet('{cfg.uri("silver", "evenements")}/*/*.parquet', hive_partitioning = true);
    CREATE OR REPLACE VIEW indicateurs AS
      SELECT * FROM read_parquet(
        '{cfg.uri("gold", "indicateurs_commune_secteur")}/*/*.parquet', hive_partitioning = true
      );
    """


def interroger_duckdb(cfg: Config, sql: str, parametres: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    connexion = connexion_duckdb(cfg)
    try:
        connexion.execute(_vues_duckdb(cfg))
        rendu = sql.format(**parametres) if parametres else sql
        curseur = connexion.execute(rendu)
        colonnes = [d[0] for d in curseur.description]
        return [dict(zip(colonnes, ligne, strict=False)) for ligne in curseur.fetchall()]
    finally:
        connexion.close()


# ---------------------------------------------------------------------------
# Athena
# ---------------------------------------------------------------------------


def interroger_athena(cfg: Config, sql: str, parametres: dict[str, Any] | None = None, attente_max: int = 120):
    """Execute une requete Athena et renvoie les lignes.

    Le meme texte SQL que DuckDB, a ceci pres que les vues sont ici des tables
    du catalogue Glue creees par `sql/athena_ddl.sql`.
    """
    athena = client_athena(cfg)
    rendu = sql.format(**parametres) if parametres else sql
    depart = athena.start_query_execution(
        QueryString=rendu,
        QueryExecutionContext={"Database": cfg.base_catalogue},
        WorkGroup=cfg.athena_workgroup,
        ResultConfiguration={"OutputLocation": cfg.uri("athena-results")},
    )
    identifiant = depart["QueryExecutionId"]
    debut = time.time()
    while True:
        etat = athena.get_query_execution(QueryExecutionId=identifiant)["QueryExecution"]["Status"]
        if etat["State"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        if time.time() - debut > attente_max:
            athena.stop_query_execution(QueryExecutionId=identifiant)
            raise TimeoutError(f"requete Athena {identifiant} au-dela de {attente_max} s")
        time.sleep(2)
    if etat["State"] != "SUCCEEDED":
        raise RuntimeError(f"requete Athena en echec : {etat.get('StateChangeReason')}")
    pages = athena.get_paginator("get_query_results").paginate(QueryExecutionId=identifiant)
    lignes: list[dict[str, Any]] = []
    colonnes: list[str] = []
    for page in pages:
        donnees = page["ResultSet"]["Rows"]
        if not colonnes:
            colonnes = [c.get("VarCharValue") for c in donnees[0]["Data"]]
            donnees = donnees[1:]
        for ligne in donnees:
            valeurs = [c.get("VarCharValue") for c in ligne["Data"]]
            lignes.append(dict(zip(colonnes, valeurs, strict=False)))
    return lignes


def interroger(cfg: Config, sql: str, parametres: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Point d'entree unique : le moteur vient de la configuration."""
    if cfg.moteur_requete.lower() == "athena":
        return interroger_athena(cfg, sql, parametres)
    return interroger_duckdb(cfg, sql, parametres)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def declarer_tables_athena(cfg: Config) -> list[str]:
    """Rejoue `sql/athena_ddl.sql` sur le vrai compte.

    Sur LocalStack en edition communautaire, Athena n'est pas emule : la
    fonction est alors sautee et DuckDB prend le relais pour la lecture.
    """
    ddl = (RACINE_SQL / "athena_ddl.sql").read_text(encoding="utf-8")
    instructions = []
    for morceau in ddl.split(";"):
        # On retire les lignes de commentaire : le premier morceau porte tout
        # l'entete du fichier et serait sinon pris pour un commentaire entier.
        corps = "\n".join(ligne for ligne in morceau.splitlines() if not ligne.strip().startswith("--")).strip()
        if corps:
            instructions.append(corps)
    executees = []
    for instruction in instructions:
        # Remplacement litteral plutot que str.format : le DDL contient
        # ${date_parution}, qui est la syntaxe de projection d'Athena et non un
        # champ de formatage Python.
        rendu = (
            instruction.replace("{bucket}", cfg.bucket)
            .replace("{prefixe}", cfg.prefixe)
            .replace("{base}", cfg.base_catalogue)
        )
        interroger_athena(cfg, rendu)
        executees.append(rendu.splitlines()[0][:80])
    return executees


def verifier_acces_s3(cfg: Config) -> dict[str, Any]:
    """Diagnostic : quelle cible, emulee ou reelle, et quels droits reels.

    Volontairement tolerant a `AccessDenied` : une cle au droit minimal ne peut
    ni lister les compartiments du compte, ni lire la region d'un compartiment.
    Ce n'est pas une erreur, c'est la preuve que la politique est bien etroite.
    Le diagnostic le dit au lieu de s'arreter.
    """
    from botocore.exceptions import ClientError

    s3 = client_s3(cfg)
    rapport: dict[str, Any] = {
        "endpoint": cfg.endpoint_url or f"https://s3.{cfg.region}.amazonaws.com",
        "emule": cfg.est_emule,
        "region": cfg.region,
        "bucket": cfg.bucket,
        "prefixe": cfg.prefixe,
        "moteur_requete": cfg.moteur_requete,
    }
    try:
        rapport["seaux_visibles"] = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    except ClientError as err:
        rapport["seaux_visibles"] = f"refuse ({err.response.get('Error', {}).get('Code')})"
    try:
        reponse = s3.list_objects_v2(Bucket=cfg.bucket, Prefix=cfg.prefixe, MaxKeys=1)
        rapport["lecture_du_prefixe"] = "ok"
        rapport["objets_deja_presents"] = reponse.get("KeyCount", 0) > 0
    except ClientError as err:
        rapport["lecture_du_prefixe"] = f"refuse ({err.response.get('Error', {}).get('Code')})"
    return rapport
