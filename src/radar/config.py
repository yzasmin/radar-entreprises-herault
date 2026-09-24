"""Configuration du radar, lue dans l'environnement.

Un seul principe : le code ne sait pas s'il parle a LocalStack ou au vrai AWS.
Seule la variable AWS_ENDPOINT_URL change. Quand elle est vide, boto3 resout
l'adresse reelle du service dans la region demandee.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEPARTEMENT_DEFAUT = "34"

# Noms des trois couches, tels qu'ils apparaissent dans les chemins S3.
# Bronze, silver et gold sont les noms d'usage de l'architecture en medaillon ;
# les garder en anglais evite d'avoir a les traduire dans le catalogue Glue.
COUCHE_BRONZE = "bronze"
COUCHE_SILVER = "silver"
COUCHE_GOLD = "gold"

# Familles d'avis publiees par le BODACC (champ familleavis de l'API Explore).
FAMILLES_BODACC = {
    "creation": "Creations",
    "immatriculation": "Immatriculations",
    "radiation": "Radiations",
    "collective": "Procedures collectives",
    "vente": "Ventes et cessions",
    "modification": "Modifications diverses",
    "dpc": "Depots des comptes",
}


def _bool_env(nom: str, defaut: bool) -> bool:
    brut = os.environ.get(nom)
    if brut is None:
        return defaut
    return brut.strip().lower() in {"1", "true", "oui", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """Parametres d'execution. Aucun secret n'est stocke ici, seulement lu."""

    # Nom du compartiment cible. Volontairement identique en emulation et sur le vrai
    # compte : la bascule ne doit changer que l'adresse du service, jamais un chemin.
    bucket: str = field(
        default_factory=lambda: os.environ.get("RADAR_BUCKET") or os.environ.get("S3_BUCKET") or "amzn-s3-seau"
    )
    prefixe: str = field(default_factory=lambda: os.environ.get("RADAR_PREFIXE", "radar"))
    # La region du compartiment `amzn-s3-seau` est eu-north-1 (Stockholm).
    # AWS_DEFAULT_REGION est lu en second : c'est le nom que connait l'outil aws.
    region: str = field(
        default_factory=lambda: os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-north-1"
    )
    endpoint_url: str | None = field(
        default_factory=lambda: os.environ.get("AWS_ENDPOINT_URL") or None
    )
    departement: str = field(default_factory=lambda: os.environ.get("RADAR_DEPARTEMENT", DEPARTEMENT_DEFAUT))

    # Entrepot : "duckdb" (lecture des memes Parquet, sans compte AWS) ou "athena".
    moteur_requete: str = field(default_factory=lambda: os.environ.get("RADAR_MOTEUR", "duckdb"))
    base_catalogue: str = field(default_factory=lambda: os.environ.get("RADAR_GLUE_DB", "radar_entreprises"))
    athena_workgroup: str = field(default_factory=lambda: os.environ.get("RADAR_ATHENA_WORKGROUP", "radar"))

    # Sources.
    url_bodacc: str = field(
        default_factory=lambda: os.environ.get(
            "RADAR_URL_BODACC",
            "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records",
        )
    )
    url_recherche_entreprises: str = field(
        default_factory=lambda: os.environ.get(
            "RADAR_URL_ENTREPRISES", "https://recherche-entreprises.api.gouv.fr/search"
        )
    )
    url_geo_communes: str = field(
        default_factory=lambda: os.environ.get(
            "RADAR_URL_GEO", "https://geo.api.gouv.fr/departements/{departement}/communes"
        )
    )

    # Garde-fous.
    enrichissement_actif: bool = field(default_factory=lambda: _bool_env("RADAR_ENRICHISSEMENT", True))
    max_sirens_enrichis: int = field(default_factory=lambda: int(os.environ.get("RADAR_MAX_SIRENS", "800")))
    requetes_par_seconde: float = field(default_factory=lambda: float(os.environ.get("RADAR_RPS", "5")))
    # Plusieurs fils attendent le reseau en parallele ; la cadence globale reste
    # bornee par requetes_par_seconde, sous la limite de 7 par seconde de l'API.
    fils_enrichissement: int = field(default_factory=lambda: int(os.environ.get("RADAR_FILS", "5")))

    @property
    def est_emule(self) -> bool:
        """Vrai tant que l'on pointe vers un emulateur local (LocalStack)."""
        return self.endpoint_url is not None

    def chemin(self, couche: str, *suffixe: str) -> str:
        """Cle S3 d'une couche, sans le schema s3://."""
        morceaux = [self.prefixe, couche, *[s.strip("/") for s in suffixe if s]]
        return "/".join(m for m in morceaux if m)

    def uri(self, couche: str, *suffixe: str) -> str:
        return f"s3://{self.bucket}/{self.chemin(couche, *suffixe)}"


def charger_config() -> Config:
    return Config()
