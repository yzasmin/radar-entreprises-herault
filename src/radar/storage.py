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
        if code not in {"404", "NoSuchBucket", "403"}:
            raise
        if code == "403":
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


def existe(cfg: Config, cle: str) -> bool:
    try:
        client_s3(cfg).head_object(Bucket=cfg.bucket, Key=cle)
        return True
    except ClientError:
        return False
