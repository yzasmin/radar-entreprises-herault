"""Extraction des sources publiques.

BODACC : API Explore v2.1 de la DILA (Opendatasoft), une requete par jour de
parution et par departement. L'API plafonne la pagination a 10 000 lignes ;
on le verifie explicitement au lieu de tronquer en silence.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


class Cadence:
    """Limiteur de debit partage entre plusieurs fils d'execution.

    L'API Recherche d'entreprises accepte 7 requetes par seconde et par adresse
    IP. Une boucle sequentielle qui attend 1/5 de seconde entre deux appels ne
    fait jamais 5 requetes par seconde : elle fait 1 / (0,2 + latence), soit
    environ 1 par seconde en pratique. On separe donc les deux choses : plusieurs
    fils attendent le reseau en parallele, et ce jeton d'entree garantit que le
    debit global reste sous la limite annoncee.
    """

    def __init__(self, par_seconde: float) -> None:
        self.intervalle = 1.0 / max(par_seconde, 0.1)
        self._verrou = threading.Lock()
        self._prochain = 0.0

    def attendre(self) -> float:
        """Bloque le temps qu'il faut, et renvoie la duree attendue."""
        with self._verrou:
            maintenant = time.monotonic()
            depart = max(maintenant, self._prochain)
            self._prochain = depart + self.intervalle
        attente = depart - maintenant
        if attente > 0:
            time.sleep(attente)
        return max(attente, 0.0)


def enrichir_sirens(cfg: Config, sirens: list[str]) -> dict[str, dict[str, Any]]:
    """Fiche d'entreprise pour chaque SIREN, via l'API Recherche d'entreprises.

    Un SIREN introuvable (entreprise non diffusible, radiee du RCS) renvoie une
    absence : c'est un resultat, pas une erreur, et le graphe continue sans.
    """
    fiches: dict[str, dict[str, Any]] = {}
    if not sirens:
        return fiches
    demandes = sirens[: cfg.max_sirens_enrichis]
    cadence = Cadence(cfg.requetes_par_seconde)

    def interroger(siren: str) -> tuple[str, dict[str, Any] | None]:
        cadence.attendre()
        try:
            charge = _get(
                cfg.url_recherche_entreprises,
                {"q": siren, "per_page": 1, "page": 1, "minimal": "true", "include": "siege"},
                essais=3,
            )
        except ExtractionError as err:
            LOG.warning("enrichissement impossible pour %s : %s", siren, err)
            return siren, None
        resultats = charge.get("results") or []
        return siren, next((r for r in resultats if r.get("siren") == siren), None)

    debut = time.monotonic()
    with ThreadPoolExecutor(max_workers=cfg.fils_enrichissement) as pool:
        for siren, trouve in pool.map(interroger, demandes):
            if trouve:
                fiches[siren] = trouve
    duree = time.monotonic() - debut
    LOG.info(
        "enrichissement : %d fiches pour %d SIREN demandes en %.1f s (%.1f req/s)",
        len(fiches),
        len(demandes),
        duree,
        len(demandes) / duree if duree else 0,
    )
    return fiches
