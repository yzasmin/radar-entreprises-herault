"""Mesure ce que chaque source publie, et enregistre le releve dans `results/`.

Les chiffres de la section « Donnees et licences » du README (volumetrie du
BODACC, poids des fichiers stock SIRENE, plafond de pagination de l'API
Recherche d'entreprises, nombre de communes de l'Herault) sont des mesures, pas
des ordres de grandeur recopies d'une documentation. Ce script les prend, les
date et les ecrit dans `results/sources_verifiees.json`, pour qu'ils soient
verifiables comme le reste.

    python scripts/verifier_sources.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radar.config import charger_config

RACINE = Path(__file__).resolve().parents[1]
AGENT = "radar-entreprises-herault/1.0 (+https://github.com/yzasmin/radar-entreprises-herault)"
DATASET_SIRENE = "5b7ffc618b4c4169d30727e0"


def _json(url: str, parametres: dict | None = None) -> dict:
    reponse = requests.get(url, params=parametres or {}, timeout=90, headers={"User-Agent": AGENT})
    reponse.raise_for_status()
    return reponse.json()


def main() -> int:
    cfg = charger_config()
    releve: dict = {"releve_le": date.today().isoformat(), "departement": cfg.departement}

    # 1. BODACC : volumetrie totale et volumetrie du departement.
    total = _json(cfg.url_bodacc, {"limit": 0})
    dept = _json(cfg.url_bodacc, {"where": f"numerodepartement='{cfg.departement}'", "limit": 0})
    derniere = _json(
        cfg.url_bodacc,
        {
            "where": f"numerodepartement='{cfg.departement}'",
            "order_by": "dateparution desc",
            "limit": 1,
            "select": "dateparution",
        },
    )
    releve["bodacc"] = {
        "url": cfg.url_bodacc,
        "licence": "Licence Ouverte (FR-LO), producteur DILA",
        "annonces_total": total.get("total_count"),
        "annonces_departement": dept.get("total_count"),
        "derniere_parution_disponible": (derniere.get("results") or [{}])[0].get("dateparution"),
        "plafond_pagination": 10000,
    }

    # 2. API Recherche d'entreprises : plafond de pagination, absence de cle.
    recherche = _json(
        cfg.url_recherche_entreprises,
        {"departement": cfg.departement, "per_page": 1, "page": 1, "minimal": "true"},
    )
    releve["recherche_entreprises"] = {
        "url": cfg.url_recherche_entreprises,
        "licence": "donnees SIRENE et RNE sous Licence Ouverte 2.0, code de l'API sous MIT",
        "cle_requise": False,
        "requetes_par_seconde": 7,
        "total_results_pour_le_departement": recherche.get("total_results"),
        "note": "total_results est le plafond de pagination, pas un comptage d'etablissements",
    }

    # 3. Fichiers stock SIRENE sur data.gouv.fr : poids reel et frequence.
    jeu = _json(f"https://www.data.gouv.fr/api/1/datasets/{DATASET_SIRENE}/")
    ressources = {}
    for ressource in jeu.get("resources", []):
        titre = ressource.get("title") or ""
        if "parquet" in titre.lower() and ressource.get("filesize"):
            if "StockEtablissement -" in titre:
                ressources["stock_etablissement_parquet_octets"] = ressource["filesize"]
            elif "StockUniteLegale -" in titre:
                ressources["stock_unite_legale_parquet_octets"] = ressource["filesize"]
    releve["sirene_stock"] = {
        "licence": jeu.get("license"),
        "frequence": jeu.get("frequency"),
        "derniere_modification": jeu.get("last_modified"),
        **ressources,
        "verdict": "ecarte : mensuel, et trop lourd pour un poste de 8 Go",
    }

    # 4. API SIRENE de l'INSEE : refus sans jeton.
    try:
        reponse = requests.get(
            "https://api.insee.fr/api-sirene/3.11/siren/478455793", timeout=30, headers={"User-Agent": AGENT}
        )
        code = reponse.status_code
    except requests.RequestException as err:  # pragma: no cover - dependance reseau
        code = f"injoignable ({err})"
    releve["sirene_api_insee"] = {
        "version": "3.11",
        "code_http_sans_jeton": code,
        "verdict": "ecarte pour l'instant : demande un compte INSEE et une cle",
    }

    # 5. Referentiel geographique.
    communes = _json(
        cfg.url_geo_communes.format(departement=cfg.departement), {"fields": "code", "format": "json"}
    )
    releve["geo_api_gouv"] = {
        "licence": "Licence Ouverte",
        "nb_communes_departement": len(communes),
    }

    chemin = RACINE / "results" / "sources_verifiees.json"
    chemin.parent.mkdir(exist_ok=True)
    chemin.write_text(json.dumps(releve, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(releve, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
