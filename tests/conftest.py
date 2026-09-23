"""Jeux d'essai. Les annonces reproduisent la forme reelle de l'API Explore :
champs imbriques livres en chaine JSON, `registre` en liste doublee, accents.
"""

from __future__ import annotations

import json

import pytest

COMMUNES = [
    # Valeurs reelles relevees sur geo.api.gouv.fr le 23/09/2026 (7 des 341 communes
    # du departement). Volontairement partiel : c'est ce qui permet de tester le cas
    # d'une commune absente du referentiel.
    {
        "code": "34172",
        "nom": "Montpellier",
        "codesPostaux": ["34000", "34070", "34080", "34090"],
        "population": 310240,
        "centre": {"type": "Point", "coordinates": [3.8742, 43.61]},
    },
    {
        "code": "34003",
        "nom": "Agde",
        "codesPostaux": ["34300"],
        "population": 29939,
        "centre": {"type": "Point", "coordinates": [3.4838, 43.3084]},
    },
    {
        "code": "34032",
        "nom": "Béziers",
        "codesPostaux": ["34500"],
        "population": 81545,
        "centre": {"type": "Point", "coordinates": [3.2342, 43.3481]},
    },
    {
        "code": "34146",
        "nom": "Lunel-Viel",
        "codesPostaux": ["34400"],
        "population": 4507,
        "centre": {"type": "Point", "coordinates": [4.0821, 43.6803]},
    },
    {
        "code": "34150",
        "nom": "Marseillan",
        "codesPostaux": ["34340"],
        "population": 8414,
        "centre": {"type": "Point", "coordinates": [3.556, 43.3543]},
    },
    {
        "code": "34154",
        "nom": "Mauguio",
        "codesPostaux": ["34130"],
        "population": 16522,
        "centre": {"type": "Point", "coordinates": [4.0128, 43.5915]},
    },
    {
        "code": "34270",
        "nom": "Saint-Jean-de-Védas",
        "codesPostaux": ["34430"],
        "population": 13328,
        "centre": {"type": "Point", "coordinates": [3.8329, 43.5732]},
    },
]

JOUR = "2026-09-23"


def _annonce(**champs):
    base = {
        "id": "X000000000000",
        "dateparution": JOUR,
        "numerodepartement": "34",
        "familleavis": "creation",
        "commercant": "SANS NOM",
        "ville": "Montpellier",
        "cp": "34000",
        "registre": [],
        "tribunal": "Greffe du Tribunal de Commerce de Montpellier",
        "listepersonnes": None,
        "listeetablissements": None,
        "acte": None,
        "modificationsgenerales": None,
        "radiationaurcs": None,
        "jugement": None,
        "url_complete": "https://www.bodacc.fr/pages/annonces-commerciales-detail/?q.id=id:X000000000000",
    }
    base.update(champs)
    return base


ANNONCES = [
    # 1. Creation d'un etablissement principal, personne physique.
    _annonce(
        id="A202601821317",
        familleavis="creation",
        commercant="TO--NGUYEN, Jean-Francois",
        ville="Montpellier",
        cp="34070",
        registre=["109482182", "109 482 182"],
        listepersonnes=json.dumps(
            {
                "personne": {
                    "typePersonne": "pp",
                    "nom": "TO--NGUYEN",
                    "prenom": "Jean-Francois",
                    "nomCommercial": "Jean-fr",
                }
            }
        ),
        listeetablissements=json.dumps(
            {"etablissement": {"origineFonds": "Création", "activite": "coursier, livraisons à domicile"}}
        ),
        acte=json.dumps({"dateImmatriculation": "2026-09-21", "creation": {"categorieCreation": "Immatriculation"}}),
    ),
    # 2. Immatriculation qui est en realite un transfert hors ressort.
    _annonce(
        id="A202601821334",
        familleavis="immatriculation",
        commercant="PELIZZARI, Jonathan",
        ville="Lunel-Viel",
        cp="34400",
        registre=["900027202", "900 027 202"],
        listepersonnes=json.dumps({"personne": {"typePersonne": "pp", "nom": "PELIZZARI", "prenom": "Jonathan"}}),
        acte=json.dumps(
            {
                "descriptif": "immatriculation suite à transfert de l'établissement principal hors ressort.",
                "immatriculation": {"categorieImmatriculation": "Immatriculation suite à transfert"},
            }
        ),
    ),
    # 3. Liquidation judiciaire par conversion d'un redressement.
    _annonce(
        id="A202601824374",
        familleavis="collective",
        commercant="BAILLEUL, Sandrine",
        ville="MONTPELLIER",
        cp="34070",
        registre=["849933825", "849 933 825"],
        listepersonnes=json.dumps({"personne": {"typePersonne": "pp", "nom": "BAILLEUL", "prenom": "Sandrine"}}),
        jugement=json.dumps(
            {
                "type": "initial",
                "famille": "Extrait de jugement",
                "nature": "Autre jugement et ordonnance",
                "date": "2026-09-17",
                "complementJugement": (
                    "Prononce la liquidation judiciaire sur conversion de la procédure de redressement"
                ),
            }
        ),
    ),
    # 4. Ouverture d'un redressement judiciaire.
    _annonce(
        id="A202601824375",
        familleavis="collective",
        commercant="ATELIER DU LITTORAL",
        ville="Agde",
        cp="34300",
        registre=["821553419", "821 553 419"],
        listepersonnes=json.dumps(
            {"personne": {"typePersonne": "pm", "denomination": "ATELIER DU LITTORAL", "formeJuridique": "SARL"}}
        ),
        jugement=json.dumps(
            {
                "famille": "Extrait de jugement",
                "nature": "Jugement d'ouverture d'une procédure de redressement judiciaire",
                "date": "2026-09-16",
                "complementJugement": "Ouverture du redressement judiciaire, période d'observation de six mois",
            }
        ),
    ),
    # 5. Vente d'un fonds avec prix, changement de commune.
    _annonce(
        id="A202601821304",
        familleavis="vente",
        commercant="POLI PLATTO",
        ville="Mauguio",
        cp="34130",
        registre=["107129215", "107 129 215", "803445667", "803 445 667"],
        listepersonnes=json.dumps({"personne": {"typePersonne": "pm", "denomination": "POLI PLATTO"}}),
        listeetablissements=json.dumps(
            {
                "etablissement": {
                    "origineFonds": "Établissement principal acquis par achat au prix stipulé de 345000.00 euros",
                    "activite": "librairie, papeterie",
                }
            }
        ),
        acte=json.dumps(
            {"descriptif": "Modification survenue sur le nom commercial", "dateCommencementActivite": "2026-09-01"}
        ),
    ),
    # 6. Radiation apres cloture de liquidation, personne morale.
    _annonce(
        id="B202601821470",
        familleavis="radiation",
        commercant="SCI DES CHAMPS",
        ville="Marseillan",
        cp="34340",
        registre=["419208939", "419 208 939"],
        listepersonnes=json.dumps(
            {"personne": {"typePersonne": "pm", "denomination": "SCI DES CHAMPS", "formeJuridique": "SCI"}}
        ),
        radiationaurcs=json.dumps({"commentaire": "Radiation suite à clôture des opérations de liquidation"}),
    ),
    # 7. Modification simple, sans transfert.
    _annonce(
        id="B202601821459",
        familleavis="modification",
        commercant="SALVIGNOL, Mathieu",
        ville="Agde",
        cp="34300",
        registre=["939625547", "939 625 547"],
        listepersonnes=json.dumps(
            {
                "personne": {
                    "typePersonne": "pp",
                    "nom": "SALVIGNOL",
                    "prenom": "Mathieu",
                    "nomCommercial": "lunette and co",
                }
            }
        ),
        modificationsgenerales=json.dumps({"descriptif": "Modification survenue sur l'activité."}),
    ),
    # 8. Modification qui est un transfert de siege.
    _annonce(
        id="B202601821460",
        familleavis="modification",
        commercant="HORIZON CONSEIL",
        ville="ST JEAN DE VEDAS",
        cp="34430",
        registre=["510654387", "510 654 387"],
        listepersonnes=json.dumps({"personne": {"typePersonne": "pm", "denomination": "HORIZON CONSEIL"}}),
        modificationsgenerales=json.dumps({"descriptif": "Transfert du siège social à Saint-Jean-de-Védas."}),
    ),
    # 9. Depot des comptes, a compter mais sans effet sur le solde.
    _annonce(
        id="C202601822618",
        familleavis="dpc",
        commercant="LE FOURNIL",
        ville="Béziers",
        cp="34500",
        registre=["752461681", "752 461 681"],
        listepersonnes=json.dumps({"personne": {"typePersonne": "pm", "denomination": "LE FOURNIL"}}),
        depot=json.dumps({"dateCloture": "2025-12-31", "typeDepot": "Comptes annuels"}),
    ),
    # 10. Doublon exact de l'annonce 1 : republication du meme identifiant.
    _annonce(
        id="A202601821317",
        familleavis="creation",
        commercant="TO--NGUYEN, Jean-Francois",
        ville="Montpellier",
        cp="34070",
        registre=["109482182"],
    ),
]

FICHES = {
    "109482182": {
        "siren": "109482182",
        "activite_principale": "53.20Z",
        "date_creation": "2026-09-01",
        "siege": {"activite_principale": "53.20Z", "tranche_effectif_salarie": "00", "commune": "34172"},
    },
    "821553419": {
        "siren": "821553419",
        "activite_principale": "43.32A",
        "date_creation": "2016-07-04",
        "siege": {"activite_principale": "43.32A", "tranche_effectif_salarie": "11", "commune": "34003"},
    },
    "849933825": {
        "siren": "849933825",
        "activite_principale": "86.90D",
        "date_creation": "2019-04-15",
        "siege": {"activite_principale": "86.90D", "tranche_effectif_salarie": "NN", "commune": "34172"},
    },
    "107129215": {
        "siren": "107129215",
        "activite_principale": "47.62Z",
        "date_creation": "2004-08-25",
        "siege": {"activite_principale": "47.62Z", "tranche_effectif_salarie": "02", "commune": "34154"},
    },
}


@pytest.fixture
def communes():
    return COMMUNES


@pytest.fixture
def index_communes():
    from radar.transform import indexer_communes

    return indexer_communes(COMMUNES)


@pytest.fixture
def annonces():
    return [dict(a) for a in ANNONCES]


@pytest.fixture
def fiches():
    return {k: dict(v) for k, v in FICHES.items()}


@pytest.fixture
def jour():
    return JOUR
