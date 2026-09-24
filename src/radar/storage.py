"""Acces S3 : exactement les memes appels boto3 sur LocalStack et sur AWS.

La seule difference tient dans `endpoint_url`. Quand la variable d'environnement
AWS_ENDPOINT_URL est absente, boto3 resout l'adresse publique du service et le
code bascule sur le vrai compte sans qu'une ligne change.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from radar.config import Config

LOG = logging.getLogger(__name__)


def client_s3(cfg: Config):
    """Client S3. `endpoint_url=None` signifie AWS reel."""
    reglages = BotoConfig(
        region_name=cfg.region,
        retries={"max_attempts": 5, "mode": "standard"},
        # LocalStack ne gere pas les adresses de type bucket.s3.amazonaws.com.
        s3={"addressing_style": "path"} if cfg.est_emule else {},
    )
    return boto3.client("s3", endpoint_url=cfg.endpoint_url, config=reglages)


def client_athena(cfg: Config):
    return boto3.client("athena", endpoint_url=cfg.endpoint_url, region_name=cfg.region)


def client_glue(cfg: Config):
    return boto3.client("glue", endpoint_url=cfg.endpoint_url, region_name=cfg.region)


def assurer_seau(cfg: Config) -> bool:
    """Cree le seau s'il manque. Vrai s'il a ete cree par cet appel.

    Sur le vrai compte, le seau vient de Terraform et cet appel ne fait que
    confirmer son existence : la cle applicative n'a pas le droit s3:CreateBucket.
    """
    s3 = client_s3(cfg)
    try:
        s3.head_bucket(Bucket=cfg.bucket)
        return False
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code")
        if code in {"403", "AccessDenied"}:
            # Cas normal avec une cle au droit minimal : elle peut ecrire sous son
            # prefixe mais pas interroger le compartiment entier. Le compartiment
            # existe, il vient de Terraform ou de la console. On continue.
            LOG.info("head_bucket refuse sur %s : la cle n'a pas ce droit, le compartiment existe deja", cfg.bucket)
            return False
        if code not in {"404", "NoSuchBucket"}:
            raise
    parametres: dict[str, Any] = {"Bucket": cfg.bucket}
    if cfg.region != "us-east-1":
        parametres["CreateBucketConfiguration"] = {"LocationConstraint": cfg.region}
    s3.create_bucket(**parametres)
    LOG.info("seau %s cree", cfg.bucket)
    return True


def ecrire_parquet(cfg: Config, table: pa.Table, cle: str) -> int:
    """Ecrit une table Arrow en Parquet compresse. Renvoie la taille en octets."""
    tampon = io.BytesIO()
    pq.write_table(table, tampon, compression="snappy")
    corps = tampon.getvalue()
    client_s3(cfg).put_object(Bucket=cfg.bucket, Key=cle, Body=corps, ContentType="application/vnd.apache.parquet")
    LOG.info("ecrit s3://%s/%s (%d octets, %d lignes)", cfg.bucket, cle, len(corps), table.num_rows)
    return len(corps)


def lire_parquet(cfg: Config, cle: str) -> pa.Table:
    objet = client_s3(cfg).get_object(Bucket=cfg.bucket, Key=cle)
    return pq.read_table(io.BytesIO(objet["Body"].read()))


def ecrire_json(cfg: Config, donnees: Any, cle: str) -> int:
    corps = json.dumps(donnees, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    client_s3(cfg).put_object(Bucket=cfg.bucket, Key=cle, Body=corps, ContentType="application/json")
    return len(corps)


def lire_json(cfg: Config, cle: str) -> Any:
    objet = client_s3(cfg).get_object(Bucket=cfg.bucket, Key=cle)
    return json.loads(objet["Body"].read().decode("utf-8"))


def lister(cfg: Config, prefixe: str) -> list[dict[str, Any]]:
    """Liste paginee des objets d'un prefixe."""
    s3 = client_s3(cfg)
    resultats: list[dict[str, Any]] = []
    jeton: str | None = None
    while True:
        parametres: dict[str, Any] = {"Bucket": cfg.bucket, "Prefix": prefixe}
        if jeton:
            parametres["ContinuationToken"] = jeton
        reponse = s3.list_objects_v2(**parametres)
        for objet in reponse.get("Contents", []):
            resultats.append({"cle": objet["Key"], "taille": objet["Size"]})
        if not reponse.get("IsTruncated"):
            return resultats
        jeton = reponse.get("NextContinuationToken")


def supprimer_prefixe(
    cfg: Config, prefixe: str, confirmer: bool = False, filtre_jour: str | None = None
) -> dict[str, Any]:
    """Supprime les objets d'un prefixe. Sans `confirmer`, ne fait que lister.

    Garde-fou volontaire : la fonction refuse tout prefixe qui ne commence pas
    par celui du projet. Une cle applicative ne doit jamais pouvoir vider un
    compartiment partage, et un appel maladroit ne doit pas pouvoir non plus.
    """
    if not prefixe.startswith(cfg.prefixe):
        raise ValueError(f"prefixe refuse : {prefixe!r} ne commence pas par {cfg.prefixe!r}")
    objets = lister(cfg, prefixe)
    if filtre_jour:
        objets = [o for o in objets if f"date_parution={filtre_jour}" in o["cle"]]
    rapport = {
        "prefixe": prefixe,
        "jour": filtre_jour,
        "nb_objets": len(objets),
        "octets": sum(o["taille"] for o in objets),
        "supprime": False,
        "exemples": [o["cle"] for o in objets[:5]],
    }
    if not confirmer or not objets:
        return rapport
    s3 = client_s3(cfg)
    for debut in range(0, len(objets), 1000):  # l'API en accepte 1000 par appel
        lot = [{"Key": o["cle"]} for o in objets[debut : debut + 1000]]
        s3.delete_objects(Bucket=cfg.bucket, Delete={"Objects": lot, "Quiet": True})
    rapport["supprime"] = True
    LOG.info("supprime %d objets sous %s", len(objets), prefixe)
    return rapport


def existe(cfg: Config, cle: str) -> bool:
    try:
        client_s3(cfg).head_object(Bucket=cfg.bucket, Key=cle)
        return True
    except ClientError:
        return False
