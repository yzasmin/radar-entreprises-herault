"""Controles de qualite bloquants.

Un controle renvoie un `Resultat`. Un controle en echec dont `bloquant` vaut
vrai fait echouer la tache Airflow, donc le graphe : la couche or n'est jamais
publiee sur des donnees qui n'ont pas passe les controles.
La sortie est aussi imprimee en tableau lisible dans le journal de la tache.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from radar.transform import SECTION_INCONNUE, TYPES_EVENEMENTS


class QualiteError(AssertionError):
    """Au moins un controle bloquant a echoue."""


@dataclass
class Resultat:
    nom: str
    reussi: bool
    bloquant: bool
    attendu: str
    observe: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def statut(self) -> str:
        if self.reussi:
            return "OK"
        return "ECHEC" if self.bloquant else "ALERTE"


def _r(nom: str, reussi: bool, attendu: str, observe: str, bloquant: bool = True, **detail: Any) -> Resultat:
    return Resultat(nom=nom, reussi=reussi, bloquant=bloquant, attendu=attendu, observe=observe, detail=detail)


# ---------------------------------------------------------------------------
# Controles de la couche argent
# ---------------------------------------------------------------------------


def controle_unicite(evenements: Sequence[dict[str, Any]]) -> Resultat:
    identifiants = [e.get("id_annonce") for e in evenements]
    doublons = [i for i, n in Counter(identifiants).items() if n > 1]
    return _r(
        "unicite_id_annonce",
        not doublons,
        "aucun identifiant d'annonce en double",
        f"{len(doublons)} identifiant(s) en double",
        exemples=doublons[:5],
    )


def controle_identifiants_non_nuls(evenements: Sequence[dict[str, Any]]) -> Resultat:
    manquants = sum(1 for e in evenements if not e.get("id_annonce") or not e.get("date_parution"))
    return _r(
        "cles_non_nulles",
        manquants == 0,
        "id_annonce et date_parution toujours renseignes",
        f"{manquants} ligne(s) incompletes",
    )


def controle_valeurs_attendues(evenements: Sequence[dict[str, Any]]) -> Resultat:
    inconnus = sorted({e.get("type_evenement") for e in evenements} - set(TYPES_EVENEMENTS))
    return _r(
        "type_evenement_dans_le_vocabulaire",
        not inconnus,
        f"types parmi {len(TYPES_EVENEMENTS)} valeurs connues",
        f"{len(inconnus)} type(s) hors vocabulaire",
        inconnus=inconnus,
    )


def controle_perimetre(evenements: Sequence[dict[str, Any]], departement: str) -> Resultat:
    hors = [
        e.get("code_commune")
        for e in evenements
        if e.get("code_commune") and not str(e["code_commune"]).startswith(departement)
    ]
    return _r(
        "perimetre_departemental",
        not hors,
        f"tous les codes commune commencent par {departement}",
        f"{len(hors)} ligne(s) hors perimetre",
        exemples=sorted(set(hors))[:5],
    )


def controle_date_logique(evenements: Sequence[dict[str, Any]], jour: str) -> Resultat:
    autres = sorted({e.get("date_parution") for e in evenements if e.get("date_parution") != jour})
    return _r(
        "partition_homogene",
        not autres,
        f"toutes les lignes portent la date de parution {jour}",
        f"{len(autres)} autre(s) date(s) dans la partition",
        dates=autres[:5],
    )


def controle_rapprochement_communes(
    evenements: Sequence[dict[str, Any]], seuil: float = 0.95
) -> Resultat:
    if not evenements:
        return _r("rapprochement_communes", True, f">= {seuil:.0%}", "partition vide", bloquant=False)
    rapproches = sum(1 for e in evenements if e.get("code_commune"))
    taux = rapproches / len(evenements)
    return _r(
        "rapprochement_communes",
        taux >= seuil,
        f"au moins {seuil:.0%} des annonces rattachees a une commune du referentiel",
        f"{taux:.2%} ({rapproches}/{len(evenements)})",
        taux=round(taux, 4),
    )


def controle_siren(evenements: Sequence[dict[str, Any]], seuil: float = 0.90) -> Resultat:
    if not evenements:
        return _r("presence_siren", True, f">= {seuil:.0%}", "partition vide", bloquant=False)
    avec = sum(1 for e in evenements if e.get("siren"))
    taux = avec / len(evenements)
    return _r(
        "presence_siren",
        taux >= seuil,
        f"au moins {seuil:.0%} des annonces portent un SIREN valide (cle de Luhn)",
        f"{taux:.2%} ({avec}/{len(evenements)})",
        bloquant=False,
        taux=round(taux, 4),
    )


def controle_volumetrie(
    nb_observe: int, historique: Iterable[int], marge_basse: float = 2.0, marge_haute: float = 1.5, plancher: int = 1
) -> Resultat:
    """Volumetrie anormale, calibree sur la distribution reelle de l'historique.

    Premiere version de ce controle : bornes a la mediane divisee ou multipliee
    par quatre. Mesure faite ensuite sur 60 jours de parution reels du BODACC
    pour l'Herault : **de 1 a 1 046 annonces par jour**, mediane 425, cinquieme
    centile 9. La dispersion n'est pas du bruit, elle est structurelle : le
    bulletin ne publie pas les memes familles d'avis tous les jours, et une
    journee a 80 annonces est aussi normale qu'une journee a 800. Le seuil a la
    mediane refusait dix de ces soixante journees, toutes legitimes.

    Version retenue : bornes issues des **centiles empiriques** de la meme
    fenetre, elargies d'une marge. Le controle ne refuse plus une journee creuse
    normale, il refuse une journee hors de tout ce que la source a produit en
    trois mois, ce qui est le signal recherche.
    """
    valeurs = sorted(v for v in historique if v > 0)
    if len(valeurs) < 10:
        return _r(
            "volumetrie_dans_la_norme",
            nb_observe >= plancher,
            f"au moins {plancher} annonce",
            f"{nb_observe} annonce(s), historique trop court pour calibrer",
            bloquant=True,
            jours_historique=len(valeurs),
        )
    centile_bas = statistics.quantiles(valeurs, n=20)[0]  # 5e centile
    centile_haut = statistics.quantiles(valeurs, n=20)[18]  # 95e centile
    bas = max(plancher, centile_bas / marge_basse)
    haut = centile_haut * marge_haute
    return _r(
        "volumetrie_dans_la_norme",
        bas <= nb_observe <= haut,
        f"entre {bas:.0f} et {haut:.0f} annonces "
        f"(centiles 5 et 95 de {len(valeurs)} jours : {centile_bas:.0f} et {centile_haut:.0f}, "
        f"marges {marge_basse:g} et {marge_haute:g})",
        f"{nb_observe} annonces",
        mediane=statistics.median(valeurs),
        centile_5=round(centile_bas, 1),
        centile_95=round(centile_haut, 1),
        borne_basse=round(bas, 1),
        borne_haute=round(haut, 1),
        jours_historique=len(valeurs),
    )


def controle_completude_extraction(nb_ecrit: int, total_source: int | None) -> Resultat:
    """La couche bronze contient-elle tout ce que la source annonce ce jour-la ?

    C'est le controle exact que la volumetrie ne sait pas faire : il ne suppose
    rien sur le rythme de publication, il compare ce qui a ete ecrit au
    `total_count` renvoye par l'API pour la meme requete. Une pagination
    interrompue ou une reponse tronquee se voit ici, et nulle part ailleurs.
    """
    if total_source is None:
        return _r(
            "completude_extraction",
            False,
            "le total annonce par la source est connu",
            "total absent des metadonnees de la couche bronze",
        )
    return _r(
        "completude_extraction",
        nb_ecrit == total_source,
        f"{total_source} annonces, soit le total annonce par l'API pour ce jour",
        f"{nb_ecrit} annonces ecrites en bronze",
        ecart=nb_ecrit - total_source,
    )


def controle_fraicheur(derniere_parution: str | None, jour: str, retard_max_jours: int = 5) -> Resultat:
    """La source publie-t-elle encore ?

    On mesure l'ecart entre la date de parution la plus recente disponible et la
    date logique traitee. Un flux arrete serait invisible autrement : le graphe
    continuerait a produire des partitions vides sans rien signaler.
    """
    if derniere_parution is None:
        return _r("fraicheur_source", False, f"parution de moins de {retard_max_jours} jours", "aucune parution lue")
    ecart = (date.fromisoformat(jour) - date.fromisoformat(derniere_parution)).days
    return _r(
        "fraicheur_source",
        ecart <= retard_max_jours,
        f"derniere parution a moins de {retard_max_jours} jours de la date traitee",
        f"{ecart} jour(s) d'ecart (derniere parution {derniere_parution})",
        ecart_jours=ecart,
        derniere_parution=derniere_parution,
    )


# ---------------------------------------------------------------------------
# Controles de la couche or
# ---------------------------------------------------------------------------


def controle_conservation(evenements: Sequence[dict[str, Any]], lignes_or: Sequence[dict[str, Any]]) -> Resultat:
    """Rien ne se perd entre l'argent et l'or : les totaux doivent coincider."""
    total_or = sum(int(ligne.get("nb_evenements") or 0) for ligne in lignes_or)
    return _r(
        "conservation_argent_vers_or",
        total_or == len(evenements),
        f"{len(evenements)} evenements agreges",
        f"{total_or} evenements dans la couche or",
        argent=len(evenements),
        or_=total_or,
    )


def controle_grain_or(lignes_or: Sequence[dict[str, Any]]) -> Resultat:
    cles = [(ligne.get("date_parution"), ligne.get("code_commune"), ligne.get("section_naf")) for ligne in lignes_or]
    doublons = [c for c, n in Counter(cles).items() if n > 1]
    return _r(
        "grain_or_unique",
        not doublons,
        "une seule ligne par (jour, commune, section NAF)",
        f"{len(doublons)} cle(s) en double",
        exemples=[str(c) for c in doublons[:5]],
    )


def controle_coherence_solde(lignes_or: Sequence[dict[str, Any]]) -> Resultat:
    faux = []
    for ligne in lignes_or:
        attendu = (
            int(ligne.get("nb_creations") or 0)
            + int(ligne.get("nb_immatriculations") or 0)
            - int(ligne.get("nb_radiations") or 0)
            - int(ligne.get("nb_liquidations") or 0)
        )
        if attendu != int(ligne.get("solde_net") or 0):
            faux.append(ligne.get("code_commune"))
    return _r(
        "coherence_solde_net",
        not faux,
        "solde_net = creations + immatriculations - radiations - liquidations",
        f"{len(faux)} ligne(s) incoherentes",
        exemples=faux[:5],
    )


def controle_bornes_or(lignes_or: Sequence[dict[str, Any]]) -> Resultat:
    negatifs = [
        ligne.get("code_commune")
        for ligne in lignes_or
        for colonne, valeur in ligne.items()
        if colonne.startswith("nb_") and int(valeur or 0) < 0
    ]
    return _r(
        "compteurs_positifs",
        not negatifs,
        "tous les compteurs nb_* sont positifs ou nuls",
        f"{len(negatifs)} valeur(s) negatives",
        exemples=negatifs[:5],
    )


def controle_couverture_naf(lignes_or: Sequence[dict[str, Any]], seuil: float = 0.50) -> Resultat:
    """Part des evenements rattaches a une section NAF connue.

    Non bloquant : l'enrichissement depend d'une API tierce, et une panne de
    celle-ci ne doit pas empecher la publication des comptes par commune.
    """
    total = sum(int(ligne.get("nb_evenements") or 0) for ligne in lignes_or)
    if not total:
        return _r("couverture_naf", True, f">= {seuil:.0%}", "partition vide", bloquant=False)
    connus = sum(
        int(ligne.get("nb_evenements") or 0) for ligne in lignes_or if ligne.get("section_naf") != SECTION_INCONNUE[0]
    )
    taux = connus / total
    return _r(
        "couverture_naf",
        taux >= seuil,
        f"au moins {seuil:.0%} des evenements rattaches a une section NAF",
        f"{taux:.2%} ({connus}/{total})",
        bloquant=False,
        taux=round(taux, 4),
    )


# ---------------------------------------------------------------------------
# Enchainement et rendu
# ---------------------------------------------------------------------------


def controler_silver(
    evenements: Sequence[dict[str, Any]],
    jour: str,
    departement: str,
    historique: Iterable[int],
    derniere_parution: str | None,
    nb_bronze: int | None = None,
    total_source: int | None = None,
) -> list[Resultat]:
    return [
        controle_completude_extraction(nb_bronze if nb_bronze is not None else len(evenements), total_source),
        controle_unicite(evenements),
        controle_identifiants_non_nuls(evenements),
        controle_valeurs_attendues(evenements),
        controle_date_logique(evenements, jour),
        controle_perimetre(evenements, departement),
        controle_volumetrie(len(evenements), historique),
        controle_fraicheur(derniere_parution, jour),
        controle_rapprochement_communes(evenements),
        controle_siren(evenements),
    ]


def controler_gold(evenements: Sequence[dict[str, Any]], lignes_or: Sequence[dict[str, Any]]) -> list[Resultat]:
    return [
        controle_grain_or(lignes_or),
        controle_conservation(evenements, lignes_or),
        controle_coherence_solde(lignes_or),
        controle_bornes_or(lignes_or),
        controle_couverture_naf(lignes_or),
    ]


def rendre_tableau(resultats: Sequence[Resultat]) -> str:
    """Tableau texte, lisible tel quel dans le journal d'une tache Airflow."""
    entetes = ("statut", "controle", "attendu", "observe")
    lignes = [(r.statut, r.nom, r.attendu, r.observe) for r in resultats]
    largeurs = [
        max(len(entetes[i]), *(len(ligne[i]) for ligne in lignes)) if lignes else len(entetes[i]) for i in range(4)
    ]
    separateur = "+" + "+".join("-" * (largeur + 2) for largeur in largeurs) + "+"

    def rendre(ligne: Sequence[str]) -> str:
        return "| " + " | ".join(valeur.ljust(largeurs[i]) for i, valeur in enumerate(ligne)) + " |"

    sortie = [separateur, rendre(entetes), separateur, *[rendre(ligne) for ligne in lignes], separateur]
    echecs = [r for r in resultats if not r.reussi and r.bloquant]
    alertes = [r for r in resultats if not r.reussi and not r.bloquant]
    sortie.append(
        f"{len(resultats)} controles : {len(resultats) - len(echecs) - len(alertes)} OK, "
        f"{len(alertes)} alerte(s), {len(echecs)} echec(s) bloquant(s)"
    )
    return "\n".join(sortie)


def exiger(resultats: Sequence[Resultat], etape: str) -> dict[str, Any]:
    """Imprime le tableau, puis leve si un controle bloquant a echoue."""
    tableau = rendre_tableau(resultats)
    print(f"\nControles de qualite : {etape}\n{tableau}\n")
    echecs = [r for r in resultats if not r.reussi and r.bloquant]
    rapport = {
        "etape": etape,
        "total": len(resultats),
        "echecs_bloquants": len(echecs),
        "alertes": sum(1 for r in resultats if not r.reussi and not r.bloquant),
        "controles": [asdict(r) | {"statut": r.statut} for r in resultats],
    }
    if echecs:
        details = "; ".join(f"{r.nom} (attendu {r.attendu}, observe {r.observe})" for r in echecs)
        raise QualiteError(f"{len(echecs)} controle(s) bloquant(s) en echec a l'etape {etape} : {details}")
    return rapport
