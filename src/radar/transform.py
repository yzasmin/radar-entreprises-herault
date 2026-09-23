"""Transformations bronze -> argent -> or.

Toutes les fonctions de ce module sont pures : elles prennent un dictionnaire
issu de l'API et renvoient une structure, sans toucher au reseau ni a S3.
C'est ce qui permet de les tester sans pile ni compte cloud.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulaire des evenements
# ---------------------------------------------------------------------------

CREATION = "creation"
IMMATRICULATION = "immatriculation"
TRANSFERT = "transfert"
VENTE_FONDS = "vente_fonds"
MODIFICATION = "modification"
RADIATION = "radiation"
SAUVEGARDE = "sauvegarde"
REDRESSEMENT = "redressement"
LIQUIDATION = "liquidation"
PLAN_CESSION = "plan_cession"
CLOTURE = "cloture_procedure"
AUTRE_JUGEMENT = "autre_jugement"
DEPOT_COMPTES = "depot_comptes"
AUTRE = "autre"

TYPES_EVENEMENTS = (
    CREATION,
    IMMATRICULATION,
    TRANSFERT,
    VENTE_FONDS,
    MODIFICATION,
    RADIATION,
    SAUVEGARDE,
    REDRESSEMENT,
    LIQUIDATION,
    PLAN_CESSION,
    CLOTURE,
    AUTRE_JUGEMENT,
    DEPOT_COMPTES,
    AUTRE,
)

# Gravite : 0 neutre, 1 signal faible, 2 signal fort, 3 defaillance averee.
# Sert a trier les signaux, jamais a affirmer un risque de credit.
GRAVITE = {
    CREATION: 0,
    IMMATRICULATION: 0,
    TRANSFERT: 0,
    VENTE_FONDS: 1,
    MODIFICATION: 0,
    DEPOT_COMPTES: 0,
    RADIATION: 1,
    CLOTURE: 1,
    SAUVEGARDE: 2,
    AUTRE_JUGEMENT: 2,
    REDRESSEMENT: 3,
    PLAN_CESSION: 3,
    LIQUIDATION: 3,
    AUTRE: 0,
}

# Les evenements qui alimentent le compte des defaillances.
DEFAILLANCES = frozenset({SAUVEGARDE, REDRESSEMENT, LIQUIDATION, PLAN_CESSION})
# Les evenements qui alimentent le compte des ouvertures.
OUVERTURES = frozenset({CREATION, IMMATRICULATION})


# ---------------------------------------------------------------------------
# Outils de texte
# ---------------------------------------------------------------------------


def sans_accents(texte: str) -> str:
    """Retire les diacritiques, sans toucher a la casse."""
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


def cle_commune(nom: str | None) -> str:
    """Cle de rapprochement d'un nom de commune.

    Les noms du BODACC sont saisis a la main : casse variable, accents
    incertains, tirets ou espaces, articles parfois postposes. On normalise
    agressivement, et on garde le code postal comme second recours.
    """
    if not nom:
        return ""
    texte = sans_accents(str(nom)).upper()
    texte = re.sub(r"\bCEDEX\b.*$", "", texte)
    texte = re.sub(r"\b(\d{1,2})\s*(ER|EME|E)\b", "", texte)  # arrondissements
    texte = re.sub(r"[^A-Z]+", " ", texte)
    texte = re.sub(r"\bST\b", "SAINT", texte)
    texte = re.sub(r"\bSTE\b", "SAINTE", texte)
    return " ".join(texte.split())


def charger_champ_json(valeur: Any) -> dict[str, Any] | None:
    """Les champs imbriques de l'API Explore arrivent en chaine JSON."""
    if valeur is None or valeur == "":
        return None
    if isinstance(valeur, dict):
        return valeur
    if isinstance(valeur, list):
        return {"liste": valeur}
    try:
        charge = json.loads(valeur)
    except (TypeError, ValueError):
        return None
    return charge if isinstance(charge, dict) else None


def _premier(valeur: Any) -> Any:
    """Le BODACC alterne entre objet unique et liste d'objets sur les memes cles."""
    if isinstance(valeur, list):
        return valeur[0] if valeur else None
    return valeur


# ---------------------------------------------------------------------------
# SIREN
# ---------------------------------------------------------------------------


def siren_valide(candidat: str) -> bool:
    """Controle de la cle de Luhn du SIREN.

    Exception documentee par l'INSEE : le SIREN de La Poste (356000000) ne
    respecte pas la cle et doit etre accepte tel quel.
    """
    chiffres = re.sub(r"\D", "", candidat or "")
    if len(chiffres) != 9:
        return False
    if chiffres == "356000000":
        return True
    somme = 0
    for position, caractere in enumerate(reversed(chiffres)):
        valeur = int(caractere)
        if position % 2 == 1:
            valeur *= 2
            if valeur > 9:
                valeur -= 9
        somme += valeur
    return somme % 10 == 0


def extraire_sirens(registre: Any) -> list[str]:
    """SIREN distincts d'une annonce, dans l'ordre d'apparition.

    Le champ `registre` contient la meme identite deux fois (compacte puis
    espacee) et, pour une vente, l'ancien puis le nouvel exploitant.
    """
    if registre is None:
        return []
    elements = registre if isinstance(registre, list) else [registre]
    vus: list[str] = []
    for element in elements:
        chiffres = re.sub(r"\D", "", str(element))
        if len(chiffres) == 9 and siren_valide(chiffres) and chiffres not in vus:
            vus.append(chiffres)
    return vus


# ---------------------------------------------------------------------------
# Classement des evenements
# ---------------------------------------------------------------------------

_MOTS_LIQUIDATION = ("liquidation judiciaire", "liquidation simplifiee")
_MOTS_REDRESSEMENT = ("redressement judiciaire",)
_MOTS_SAUVEGARDE = ("sauvegarde",)
_MOTS_PLAN_CESSION = ("plan de cession", "cession totale", "arrete le plan de cession")
_MOTS_CLOTURE = ("cloture", "clôture")


def _texte_jugement(jugement: dict[str, Any] | None) -> str:
    if not jugement:
        return ""
    morceaux = [
        str(jugement.get("nature") or ""),
        str(jugement.get("famille") or ""),
        str(jugement.get("complementJugement") or ""),
    ]
    return sans_accents(" ".join(morceaux)).lower()


def classer_jugement(jugement: dict[str, Any] | None) -> str:
    """Type d'evenement derive du texte du jugement.

    L'ordre des tests compte : une conversion de redressement en liquidation
    contient les deux mots et doit compter comme une liquidation.
    """
    texte = _texte_jugement(jugement)
    if not texte:
        return AUTRE_JUGEMENT
    if any(mot in texte for mot in _MOTS_CLOTURE) and not any(mot in texte for mot in _MOTS_PLAN_CESSION):
        return CLOTURE
    if any(mot in texte for mot in _MOTS_LIQUIDATION):
        return LIQUIDATION
    if any(mot in texte for mot in _MOTS_PLAN_CESSION):
        return PLAN_CESSION
    if any(mot in texte for mot in _MOTS_REDRESSEMENT):
        return REDRESSEMENT
    if any(mot in texte for mot in _MOTS_SAUVEGARDE):
        return SAUVEGARDE
    return AUTRE_JUGEMENT


def est_transfert(ligne: dict[str, Any]) -> bool:
    """Un transfert se lit dans l'acte ou dans la modification, jamais dans la famille."""
    acte = charger_champ_json(ligne.get("acte")) or {}
    modif = charger_champ_json(ligne.get("modificationsgenerales")) or {}
    textes = [
        str(acte.get("descriptif") or ""),
        str((_premier(acte.get("immatriculation")) or {}).get("categorieImmatriculation") or "")
        if isinstance(acte.get("immatriculation"), dict | list)
        else "",
        str(modif.get("descriptif") or ""),
    ]
    joint = sans_accents(" ".join(textes)).lower()
    return "transfert" in joint or "transfere" in joint


def classer_evenement(ligne: dict[str, Any]) -> str:
    """Type normalise d'une annonce BODACC."""
    famille = str(ligne.get("familleavis") or "").lower()
    if famille == "collective":
        return classer_jugement(charger_champ_json(ligne.get("jugement")))
    if famille == "vente":
        return VENTE_FONDS
    if famille == "dpc":
        return DEPOT_COMPTES
    if famille == "radiation":
        return RADIATION
    if famille in {"creation", "immatriculation"}:
        if est_transfert(ligne):
            return TRANSFERT
        return CREATION if famille == "creation" else IMMATRICULATION
    if famille == "modification":
        return TRANSFERT if est_transfert(ligne) else MODIFICATION
    return AUTRE


def gravite(type_evenement: str) -> int:
    return GRAVITE.get(type_evenement, 0)


# ---------------------------------------------------------------------------
# Montant d'une vente de fonds
# ---------------------------------------------------------------------------

_MOTIF_PRIX = re.compile(r"prix\s+(?:stipul\w*|convenu\w*|de\s+vente)?\s*(?:de\s+)?([\d\s .,]{3,20})\s*euros?", re.I)


def montant_vente(texte: str | None) -> float | None:
    """Prix annonce d'un fonds, lu dans le texte libre de l'annonce.

    Le BODACC ecrit par exemple « acquis par achat au prix stipule de
    345000.00 euros ». Le montant est du texte libre : on renvoie None des
    qu'il n'est pas lisible sans ambiguite, plutot qu'un zero trompeur.
    """
    if not texte:
        return None
    trouve = _MOTIF_PRIX.search(sans_accents(str(texte)))
    if not trouve:
        return None
    brut = trouve.group(1).replace(" ", "").replace(" ", "")
    if brut.count(",") == 1 and brut.count(".") == 0:
        brut = brut.replace(",", ".")
    else:
        brut = brut.replace(",", "")
    # Un point qui n'est pas suivi de deux decimales est un separateur de milliers.
    if "." in brut and len(brut.split(".")[-1]) != 2:
        brut = brut.replace(".", "")
    try:
        valeur = float(brut)
    except ValueError:
        return None
    return valeur if 0 < valeur < 1e10 else None


def montant_annonce(ligne: dict[str, Any]) -> float | None:
    acte = charger_champ_json(ligne.get("acte")) or {}
    etabs = charger_champ_json(ligne.get("listeetablissements")) or {}
    etab = _premier(etabs.get("etablissement")) or {}
    for texte in (etab.get("origineFonds"), acte.get("descriptif")):
        valeur = montant_vente(texte)
        if valeur is not None:
            return valeur
    return None


# ---------------------------------------------------------------------------
# Identite de l'entreprise
# ---------------------------------------------------------------------------


def denomination(ligne: dict[str, Any]) -> str:
    """Nom lisible : raison sociale pour une personne morale, nom et prenom sinon."""
    personnes = charger_champ_json(ligne.get("listepersonnes")) or {}
    personne = _premier(personnes.get("personne")) or {}
    for cle in ("denomination", "nomCommercial"):
        valeur = personne.get(cle)
        if valeur:
            return str(valeur).strip()
    nom = str(personne.get("nom") or "").strip()
    prenom = str(personne.get("prenom") or "").strip()
    if nom:
        return f"{nom} {prenom}".strip()
    return str(ligne.get("commercant") or "").strip()


def activite_declaree(ligne: dict[str, Any]) -> str | None:
    etabs = charger_champ_json(ligne.get("listeetablissements")) or {}
    etab = _premier(etabs.get("etablissement")) or {}
    if etab.get("activite"):
        return str(etab["activite"]).strip()
    personnes = charger_champ_json(ligne.get("listepersonnes")) or {}
    personne = _premier(personnes.get("personne")) or {}
    return str(personne["activite"]).strip() if personne.get("activite") else None


# ---------------------------------------------------------------------------
# Referentiel geographique
# ---------------------------------------------------------------------------


def indexer_communes(communes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Deux index : par nom normalise, et par code postal.

    Un code postal peut couvrir plusieurs communes : dans ce cas il ne tranche
    rien et sert seulement de repli quand le nom est introuvable.
    """
    par_nom: dict[str, dict[str, Any]] = {}
    par_cp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for commune in communes:
        fiche = {
            "code_commune": commune["code"],
            "nom_commune": commune["nom"],
            "population": commune.get("population"),
            "latitude": (commune.get("centre") or {}).get("coordinates", [None, None])[1],
            "longitude": (commune.get("centre") or {}).get("coordinates", [None, None])[0],
        }
        par_nom[cle_commune(commune["nom"])] = fiche
        for code_postal in commune.get("codesPostaux") or []:
            par_cp[str(code_postal)].append(fiche)
    return {"par_nom": par_nom, "par_cp": dict(par_cp)}


def resoudre_commune(ville: str | None, code_postal: str | None, index: dict[str, Any]) -> dict[str, Any]:
    """Rapproche une annonce d'une commune du referentiel.

    Trois tentatives, dans cet ordre : nom normalise, code postal sans ambiguite,
    nom prefixe (« Montpellier 3 » pour « Montpellier »). L'echec est explicite,
    il n'invente jamais de commune.
    """
    par_nom = index.get("par_nom", {})
    par_cp = index.get("par_cp", {})
    cle = cle_commune(ville)
    if cle and cle in par_nom:
        return {**par_nom[cle], "rapprochement": "nom"}
    code = re.sub(r"\D", "", str(code_postal or ""))
    if code and len(code) == 5:
        candidats = par_cp.get(code, [])
        if len(candidats) == 1:
            return {**candidats[0], "rapprochement": "code_postal"}
        if len(candidats) > 1 and cle:
            exact = [c for c in candidats if cle_commune(c["nom_commune"]) == cle]
            if len(exact) == 1:
                return {**exact[0], "rapprochement": "code_postal_et_nom"}
    if cle:
        prefixes = [f for nom, f in par_nom.items() if cle.startswith(nom + " ") or nom.startswith(cle + " ")]
        if len(prefixes) == 1:
            return {**prefixes[0], "rapprochement": "prefixe"}
    return {
        "code_commune": None,
        "nom_commune": (ville or "").strip() or None,
        "population": None,
        "latitude": None,
        "longitude": None,
        "rapprochement": "echec",
    }


# ---------------------------------------------------------------------------
# Secteurs (sections NAF rev. 2)
# ---------------------------------------------------------------------------

# Bornes de divisions NAF rev. 2 -> section. Source : nomenclature INSEE NAF rev. 2.
_SECTIONS = [
    ((1, 3), "A", "Agriculture, sylviculture et peche"),
    ((5, 9), "B", "Industries extractives"),
    ((10, 33), "C", "Industrie manufacturiere"),
    ((35, 35), "D", "Production et distribution d'energie"),
    ((36, 39), "E", "Eau, assainissement, dechets"),
    ((41, 43), "F", "Construction"),
    ((45, 47), "G", "Commerce, reparation d'automobiles"),
    ((49, 53), "H", "Transports et entreposage"),
    ((55, 56), "I", "Hebergement et restauration"),
    ((58, 63), "J", "Information et communication"),
    ((64, 66), "K", "Activites financieres et d'assurance"),
    ((68, 68), "L", "Activites immobilieres"),
    ((69, 75), "M", "Activites specialisees, scientifiques et techniques"),
    ((77, 82), "N", "Services administratifs et de soutien"),
    ((84, 84), "O", "Administration publique"),
    ((85, 85), "P", "Enseignement"),
    ((86, 88), "Q", "Sante humaine et action sociale"),
    ((90, 93), "R", "Arts, spectacles et activites recreatives"),
    ((94, 96), "S", "Autres activites de services"),
    ((97, 98), "T", "Activites des menages employeurs"),
    ((99, 99), "U", "Activites extra-territoriales"),
]

SECTION_INCONNUE = ("Z", "Secteur non renseigne")


def section_naf(code_naf: str | None) -> tuple[str, str]:
    """Section (lettre) et libelle a partir d'un code NAF de type 56.10C ou 5610C."""
    if not code_naf:
        return SECTION_INCONNUE
    chiffres = re.sub(r"\D", "", str(code_naf))
    if len(chiffres) < 2:
        return SECTION_INCONNUE
    try:
        division = int(chiffres[:2])
    except ValueError:
        return SECTION_INCONNUE
    for (bas, haut), lettre, libelle in _SECTIONS:
        if bas <= division <= haut:
            return lettre, libelle
    return SECTION_INCONNUE


# ---------------------------------------------------------------------------
# Couche argent
# ---------------------------------------------------------------------------

COLONNES_ARGENT = (
    "id_annonce",
    "date_parution",
    "type_evenement",
    "famille_bodacc",
    "gravite",
    "siren",
    "denomination",
    "activite_declaree",
    "code_commune",
    "nom_commune",
    "code_postal",
    "population_commune",
    "latitude",
    "longitude",
    "tribunal",
    "date_jugement",
    "montant_vente_eur",
    "section_naf",
    "libelle_section_naf",
    "code_naf",
    "tranche_effectif",
    "date_creation_entreprise",
    "url_annonce",
)


def normaliser_annonce(
    ligne: dict[str, Any],
    index_communes: dict[str, Any],
    fiches_entreprises: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Une annonce brute devient une ligne d'evenement typee."""
    fiches = fiches_entreprises or {}
    sirens = extraire_sirens(ligne.get("registre"))
    siren = sirens[0] if sirens else None
    type_evenement = classer_evenement(ligne)
    commune = resoudre_commune(ligne.get("ville"), ligne.get("cp"), index_communes)
    jugement = charger_champ_json(ligne.get("jugement")) or {}
    fiche = fiches.get(siren or "", {})
    siege = fiche.get("siege") or {}
    code_naf = fiche.get("activite_principale") or siege.get("activite_principale")
    lettre, libelle = section_naf(code_naf)
    return {
        "id_annonce": ligne.get("id"),
        "date_parution": ligne.get("dateparution"),
        "type_evenement": type_evenement,
        "famille_bodacc": ligne.get("familleavis"),
        "gravite": gravite(type_evenement),
        "siren": siren,
        "denomination": denomination(ligne),
        "activite_declaree": activite_declaree(ligne),
        "code_commune": commune["code_commune"],
        "nom_commune": commune["nom_commune"],
        "code_postal": str(ligne.get("cp") or "") or None,
        "population_commune": commune["population"],
        "latitude": commune["latitude"],
        "longitude": commune["longitude"],
        "tribunal": ligne.get("tribunal"),
        "date_jugement": jugement.get("date"),
        "montant_vente_eur": montant_annonce(ligne),
        "section_naf": lettre,
        "libelle_section_naf": libelle,
        "code_naf": code_naf,
        "tranche_effectif": siege.get("tranche_effectif_salarie") or fiche.get("tranche_effectif_salarie"),
        "date_creation_entreprise": fiche.get("date_creation") or siege.get("date_creation"),
        "url_annonce": ligne.get("url_complete"),
    }


def construire_argent(
    lignes: Iterable[dict[str, Any]],
    index_communes: dict[str, Any],
    fiches_entreprises: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Couche argent : un evenement par annonce, dedoublonne sur l'identifiant.

    Le BODACC republie parfois la meme annonce (avis rectificatif portant le
    meme identifiant). On garde la premiere occurrence et on compte les autres.
    """
    vus: set[str] = set()
    sortie: list[dict[str, Any]] = []
    for ligne in lignes:
        identifiant = ligne.get("id")
        if identifiant in vus:
            continue
        vus.add(identifiant)
        sortie.append(normaliser_annonce(ligne, index_communes, fiches_entreprises))
    sortie.sort(key=lambda e: (e["date_parution"] or "", e["id_annonce"] or ""))
    return sortie


# ---------------------------------------------------------------------------
# Couche or
# ---------------------------------------------------------------------------


def agreger_par_commune_secteur(evenements: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Indicateurs quotidiens par commune et par section NAF.

    `solde_net` = ouvertures (creations et immatriculations) moins fermetures
    (radiations et liquidations). Un transfert n'entre dans aucun des deux :
    l'entreprise ne disparait pas, elle bouge.
    """
    paniers: dict[tuple, Counter] = defaultdict(Counter)
    contextes: dict[tuple, dict[str, Any]] = {}
    montants: dict[tuple, float] = defaultdict(float)
    for evenement in evenements:
        cle = (
            evenement.get("date_parution"),
            evenement.get("code_commune"),
            evenement.get("section_naf"),
        )
        paniers[cle][evenement["type_evenement"]] += 1
        paniers[cle]["total"] += 1
        if evenement.get("montant_vente_eur"):
            montants[cle] += float(evenement["montant_vente_eur"])
        contextes.setdefault(
            cle,
            {
                "nom_commune": evenement.get("nom_commune"),
                "population_commune": evenement.get("population_commune"),
                "libelle_section_naf": evenement.get("libelle_section_naf"),
                "latitude": evenement.get("latitude"),
                "longitude": evenement.get("longitude"),
            },
        )
    sortie: list[dict[str, Any]] = []
    for (jour, code_commune, section), compteur in paniers.items():
        contexte = contextes[(jour, code_commune, section)]
        ouvertures = sum(compteur[t] for t in OUVERTURES)
        defaillances = sum(compteur[t] for t in DEFAILLANCES)
        fermetures = compteur[RADIATION] + compteur[LIQUIDATION]
        sortie.append(
            {
                "date_parution": jour,
                "code_commune": code_commune,
                "nom_commune": contexte["nom_commune"],
                "population_commune": contexte["population_commune"],
                "latitude": contexte["latitude"],
                "longitude": contexte["longitude"],
                "section_naf": section,
                "libelle_section_naf": contexte["libelle_section_naf"],
                "nb_creations": compteur[CREATION],
                "nb_immatriculations": compteur[IMMATRICULATION],
                "nb_transferts": compteur[TRANSFERT],
                "nb_ventes_fonds": compteur[VENTE_FONDS],
                "nb_modifications": compteur[MODIFICATION],
                "nb_radiations": compteur[RADIATION],
                "nb_sauvegardes": compteur[SAUVEGARDE],
                "nb_redressements": compteur[REDRESSEMENT],
                "nb_liquidations": compteur[LIQUIDATION],
                "nb_plans_cession": compteur[PLAN_CESSION],
                "nb_defaillances": defaillances,
                "nb_depots_comptes": compteur[DEPOT_COMPTES],
                "nb_evenements": compteur["total"],
                "solde_net": ouvertures - fermetures,
                "montant_ventes_eur": round(montants[(jour, code_commune, section)], 2) or None,
            }
        )
    sortie.sort(
        key=lambda ligne: (
            ligne["date_parution"] or "",
            ligne["code_commune"] or "",
            ligne["section_naf"] or "",
        )
    )
    return sortie


def signaux_prioritaires(evenements: Iterable[dict[str, Any]], gravite_min: int = 2) -> list[dict[str, Any]]:
    """Liste triee des evenements a regarder en premier : defaillances d'abord."""
    retenus = [e for e in evenements if e.get("gravite", 0) >= gravite_min]
    retenus.sort(
        key=lambda e: (
            -int(e.get("gravite") or 0),
            e.get("nom_commune") or "",
            e.get("denomination") or "",
        )
    )
    return retenus


def synthese_journaliere(evenements: list[dict[str, Any]], lignes_or: list[dict[str, Any]]) -> dict[str, Any]:
    """Chiffres du jour, ceux qui sont publies."""
    compteur = Counter(e["type_evenement"] for e in evenements)
    communes = {e["code_commune"] for e in evenements if e["code_commune"]}
    sections = Counter(e["section_naf"] for e in evenements)
    montants = [e["montant_vente_eur"] for e in evenements if e.get("montant_vente_eur")]
    non_rapproches = sum(1 for e in evenements if not e["code_commune"])
    return {
        "nb_evenements": len(evenements),
        "nb_lignes_or": len(lignes_or),
        "nb_communes_touchees": len(communes),
        "nb_sans_commune": non_rapproches,
        "taux_rapprochement_commune": round(1 - non_rapproches / len(evenements), 4) if evenements else None,
        "nb_avec_siren": sum(1 for e in evenements if e["siren"]),
        "nb_avec_naf": sum(1 for e in evenements if e["section_naf"] != SECTION_INCONNUE[0]),
        "par_type": dict(sorted(compteur.items())),
        "par_section": dict(sorted(sections.items())),
        "nb_defaillances": sum(compteur[t] for t in DEFAILLANCES),
        "nb_ouvertures": sum(compteur[t] for t in OUVERTURES),
        "solde_net": sum(ligne["solde_net"] for ligne in lignes_or),
        "nb_ventes_avec_montant": len(montants),
        "montant_total_ventes_eur": round(sum(montants), 2) if montants else None,
    }


def fenetre_glissante(lignes_or: Iterable[dict[str, Any]], jour_fin: str, jours: int = 30) -> list[dict[str, Any]]:
    """Cumul par commune et section sur les N derniers jours de parution."""
    fin = date.fromisoformat(jour_fin)
    debut = fin.toordinal() - jours + 1
    cumuls: dict[tuple, Counter] = defaultdict(Counter)
    contextes: dict[tuple, dict[str, Any]] = {}
    for ligne in lignes_or:
        jour = ligne.get("date_parution")
        if not jour:
            continue
        ordinal = date.fromisoformat(jour).toordinal()
        if not (debut <= ordinal <= fin.toordinal()):
            continue
        cle = (ligne["code_commune"], ligne["section_naf"])
        for colonne, valeur in ligne.items():
            if colonne.startswith("nb_") or colonne == "solde_net":
                cumuls[cle][colonne] += int(valeur or 0)
        contextes.setdefault(
            cle,
            {"nom_commune": ligne.get("nom_commune"), "libelle_section_naf": ligne.get("libelle_section_naf")},
        )
    sortie = []
    for (code_commune, section), compteur in cumuls.items():
        sortie.append(
            {
                "jour_fin": jour_fin,
                "fenetre_jours": jours,
                "code_commune": code_commune,
                "section_naf": section,
                **contextes[(code_commune, section)],
                **dict(compteur),
            }
        )
    sortie.sort(key=lambda ligne: (-(ligne.get("nb_evenements") or 0), ligne["code_commune"] or ""))
    return sortie
