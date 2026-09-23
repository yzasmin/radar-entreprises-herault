"""Extraction des sources publiques.

BODACC : API Explore v2.1 de la DILA (Opendatasoft), une requete par jour de
parution et par departement. L'API plafonne la pagination a 10 000 lignes ;
on le verifie explicitement au lieu de tronquer en silence.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import requests

from radar.config import Config

LOG = logging.getLogger(__name__)

PLAFOND_PAGINATION = 10_000
TAILLE_PAGE = 100
AGENT = "radar-entreprises-herault/1.0 (+https://github.com/yzasmin/radar-entreprises-herault)"


class ExtractionError(RuntimeError):
    """Echec d'extraction : on prefere arreter le graphe plutot que publier un trou."""


def _get(url: str, parametres: dict[str, Any], essais: int = 4, delai: float = 2.0) -> dict[str, Any]:
    derniere: Exception | None = None
    for tentative in range(1, essais + 1):
        try:
            reponse = requests.get(url, params=parametres, timeout=60, headers={"User-Agent": AGENT})
            if reponse.status_code == 429:
                attente = float(reponse.headers.get("Retry-After", delai * tentative))
                LOG.warning("429 sur %s, attente %.1f s", url, attente)
                time.sleep(attente)
                continue
            reponse.raise_for_status()
            return reponse.json()
        except Exception as err:
            derniere = err
            if tentative == essais:
                break
            time.sleep(delai * tentative)
    raise ExtractionError(f"echec apres {essais} tentatives sur {url}: {derniere}") from derniere


def compter_bodacc(cfg: Config, jour: str, departement: str | None = None) -> int:
    """Nombre d'annonces publiees ce jour-la pour le departement."""
    dept = departement or cfg.departement
    charge = _get(
        cfg.url_bodacc,
        {"where": f"numerodepartement='{dept}' and dateparution=date'{jour}'", "limit": 0},
    )
    return int(charge.get("total_count", 0))


def extraire_bodacc(cfg: Config, jour: str, departement: str | None = None) -> list[dict[str, Any]]:
    """Toutes les annonces d'un jour de parution, pour un departement."""
    dept = departement or cfg.departement
    total = compter_bodacc(cfg, jour, dept)
    if total > PLAFOND_PAGINATION:
        raise ExtractionError(
            f"{total} annonces le {jour} pour le departement {dept} : au-dessus du plafond de pagination "
            f"de {PLAFOND_PAGINATION} de l'API Explore. Decouper la requete par famille d'avis."
        )
    lignes: list[dict[str, Any]] = []
    decalage = 0
    while decalage < total:
        charge = _get(
            cfg.url_bodacc,
            {
                "where": f"numerodepartement='{dept}' and dateparution=date'{jour}'",
                "limit": TAILLE_PAGE,
                "offset": decalage,
                "order_by": "numeroannonce",
            },
        )
        page = charge.get("results", [])
        if not page:
            break
        lignes.extend(page)
        decalage += len(page)
    if len(lignes) != total:
        raise ExtractionError(f"pagination incomplete : {len(lignes)} lignes recuperees pour {total} annoncees")
    LOG.info("BODACC %s departement %s : %d annonces", jour, dept, len(lignes))
    return lignes


def derniere_parution_disponible(cfg: Config, departement: str | None = None) -> str | None:
    """Date de parution la plus recente presente dans la source : mesure de fraicheur."""
    dept = departement or cfg.departement
    charge = _get(
        cfg.url_bodacc,
        {
            "where": f"numerodepartement='{dept}'",
            "order_by": "dateparution desc",
            "limit": 1,
            "select": "dateparution",
        },
    )
    resultats = charge.get("results") or []
    return resultats[0].get("dateparution") if resultats else None


def historique_volumetrie(cfg: Config, jour_fin: str, jours: int = 60, departement: str | None = None) -> list[int]:
    """Nombre d'annonces par jour de parution sur la fenetre precedente.

    Sert a calibrer le controle de volumetrie anormale sans coder de seuil en dur.
    """
    from datetime import date, timedelta

    dept = departement or cfg.departement
    fin = date.fromisoformat(jour_fin)
    debut = fin - timedelta(days=jours)
    charge = _get(
        cfg.url_bodacc,
        {
            "where": f"numerodepartement='{dept}' and dateparution>=date'{debut.isoformat()}' "
            f"and dateparution<date'{fin.isoformat()}'",
            "group_by": "dateparution",
            "select": "count(*) as n",
            "limit": 100,
        },
    )
    return [int(ligne["n"]) for ligne in charge.get("results", []) if ligne.get("n")]


def communes_du_departement(cfg: Config, departement: str | None = None) -> list[dict[str, Any]]:
    """Referentiel geographique : code INSEE, nom, codes postaux, population, centroide."""
    dept = departement or cfg.departement
    url = cfg.url_geo_communes.format(departement=dept)
    reponse = requests.get(
        url,
        params={"fields": "nom,code,codesPostaux,population,centre", "format": "json"},
        timeout=60,
        headers={"User-Agent": AGENT},
    )
    reponse.raise_for_status()
    return reponse.json()


def _fragments_siren(sirens: list[str], taille: int) -> Iterator[list[str]]:
    for debut in range(0, len(sirens), taille):
        yield sirens[debut : debut + taille]


def enrichir_sirens(cfg: Config, sirens: list[str]) -> dict[str, dict[str, Any]]:
    """Fiche d'entreprise pour chaque SIREN, via l'API Recherche d'entreprises.

    L'API accepte 7 requetes par seconde et par adresse IP : on reste en dessous.
    Un SIREN introuvable (entreprise non diffusible) renvoie simplement une
    absence, ce qui est un resultat et non une erreur.
    """
    fiches: dict[str, dict[str, Any]] = {}
    if not sirens:
        return fiches
    pause = 1.0 / max(cfg.requetes_par_seconde, 0.5)
    for siren in sirens[: cfg.max_sirens_enrichis]:
        try:
            charge = _get(
                cfg.url_recherche_entreprises,
                {"q": siren, "per_page": 1, "page": 1, "minimal": "true", "include": "siege"},
                essais=3,
            )
        except ExtractionError as err:
            LOG.warning("enrichissement impossible pour %s : %s", siren, err)
            time.sleep(pause)
            continue
        resultats = charge.get("results") or []
        trouve = next((r for r in resultats if r.get("siren") == siren), None)
        if trouve:
            fiches[siren] = trouve
        time.sleep(pause)
    LOG.info("enrichissement : %d fiches pour %d SIREN demandes", len(fiches), len(sirens))
    return fiches
