"""Tests de la logique de transformation.

C'est ici que vit la valeur du pipeline : classer un jugement, reconnaitre un
transfert, lire un montant en texte libre, rattacher une commune saisie a la
main. Ces tests tournent sans reseau, sans S3 et sans Airflow.
"""

from __future__ import annotations

import pytest

from radar import transform as t

# ---------------------------------------------------------------------------
# SIREN
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidat,attendu",
    [
        ("752461681", True),
        ("752 461 681", True),
        ("849933825", True),
        ("356000000", True),  # La Poste, exception documentee par l'INSEE
        ("752461682", False),  # cle de Luhn fausse
        ("12345678", False),  # huit chiffres
        ("1234567890", False),  # dix chiffres
        ("", False),
        ("abcdefghi", False),
    ],
)
def test_siren_valide(candidat, attendu):
    assert t.siren_valide(candidat) is attendu


def test_extraire_sirens_dedoublonne_la_forme_espacee():
    assert t.extraire_sirens(["752461681", "752 461 681"]) == ["752461681"]


def test_extraire_sirens_garde_l_ordre_vendeur_puis_acheteur():
    registre = ["107129215", "107 129 215", "803445667", "803 445 667"]
    assert t.extraire_sirens(registre) == ["107129215", "803445667"]


def test_extraire_sirens_ecarte_les_identifiants_invalides():
    assert t.extraire_sirens(["000000001", "752461681"]) == ["752461681"]


def test_extraire_sirens_sur_valeur_absente():
    assert t.extraire_sirens(None) == []
    assert t.extraire_sirens("752461681") == ["752461681"]


# ---------------------------------------------------------------------------
# Classement des evenements
# ---------------------------------------------------------------------------


def test_conversion_en_liquidation_compte_comme_une_liquidation():
    """Le texte contient « redressement » et « liquidation » : l'ordre des tests decide."""
    jugement = {
        "nature": "Autre jugement et ordonnance",
        "complementJugement": "Prononce la liquidation judiciaire sur conversion de la procédure de redressement",
    }
    assert t.classer_jugement(jugement) == t.LIQUIDATION


def test_ouverture_de_redressement():
    jugement = {"nature": "Jugement d'ouverture d'une procédure de redressement judiciaire"}
    assert t.classer_jugement(jugement) == t.REDRESSEMENT


def test_sauvegarde():
    assert t.classer_jugement({"nature": "Jugement d'ouverture d'une procédure de sauvegarde"}) == t.SAUVEGARDE


def test_plan_de_cession():
    jugement = {"complementJugement": "Arrête le plan de cession totale de l'entreprise"}
    assert t.classer_jugement(jugement) == t.PLAN_CESSION


def test_cloture_n_est_pas_une_defaillance_nouvelle():
    jugement = {"complementJugement": "Clôture pour insuffisance d'actif"}
    assert t.classer_jugement(jugement) == t.CLOTURE
    assert t.CLOTURE not in t.DEFAILLANCES


def test_jugement_vide_reste_classe():
    assert t.classer_jugement(None) == t.AUTRE_JUGEMENT
    assert t.classer_jugement({}) == t.AUTRE_JUGEMENT


def test_immatriculation_par_transfert_n_est_pas_une_creation(annonces):
    ligne = next(a for a in annonces if a["id"] == "A202601821334")
    assert t.est_transfert(ligne) is True
    assert t.classer_evenement(ligne) == t.TRANSFERT


def test_creation_reelle_reste_une_creation(annonces):
    ligne = next(a for a in annonces if a["id"] == "A202601821317" and a.get("acte"))
    assert t.classer_evenement(ligne) == t.CREATION


def test_modification_de_siege_devient_un_transfert(annonces):
    ligne = next(a for a in annonces if a["id"] == "B202601821460")
    assert t.classer_evenement(ligne) == t.TRANSFERT


def test_modification_d_activite_reste_une_modification(annonces):
    ligne = next(a for a in annonces if a["id"] == "B202601821459")
    assert t.classer_evenement(ligne) == t.MODIFICATION


def test_gravite_ordonne_les_signaux():
    assert t.gravite(t.LIQUIDATION) > t.gravite(t.SAUVEGARDE) > t.gravite(t.RADIATION) >= t.gravite(t.CREATION)


# ---------------------------------------------------------------------------
# Montant d'une vente
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("acquis par achat au prix stipulé de 345000.00 euros", 345000.00),
        ("au prix stipulé de 120 000 euros", 120000.0),
        ("prix de 85 500,50 euros", 85500.50),
        ("au prix convenu de 1.250.000 euros", 1250000.0),
        ("Création", None),
        (None, None),
        ("", None),
        ("apport en nature sans prix indiqué", None),
    ],
)
def test_montant_vente(texte, attendu):
    assert t.montant_vente(texte) == attendu


def test_montant_annonce_lit_l_origine_du_fonds(annonces):
    ligne = next(a for a in annonces if a["id"] == "A202601821304")
    assert t.montant_annonce(ligne) == 345000.00


def test_montant_absent_ne_devient_pas_zero(annonces):
    ligne = next(a for a in annonces if a["id"] == "B202601821459")
    assert t.montant_annonce(ligne) is None


# ---------------------------------------------------------------------------
# Rattachement des communes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "brut,attendu",
    [
        ("Montpellier", "MONTPELLIER"),
        ("MONTPELLIER", "MONTPELLIER"),
        ("Béziers", "BEZIERS"),
        ("Saint-Jean-de-Védas", "SAINT JEAN DE VEDAS"),
        ("ST JEAN DE VEDAS", "SAINT JEAN DE VEDAS"),
        ("Montpellier Cedex 5", "MONTPELLIER"),
        ("  agde  ", "AGDE"),
        (None, ""),
    ],
)
def test_cle_commune(brut, attendu):
    assert t.cle_commune(brut) == attendu


def test_resoudre_commune_par_le_nom(index_communes):
    trouve = t.resoudre_commune("Béziers", "34500", index_communes)
    assert trouve["code_commune"] == "34032"
    assert trouve["rapprochement"] == "nom"


def test_resoudre_commune_par_abreviation_saint(index_communes):
    trouve = t.resoudre_commune("ST JEAN DE VEDAS", "34430", index_communes)
    assert trouve["code_commune"] == "34270"


def test_resoudre_commune_par_code_postal_quand_le_nom_est_inconnu(index_communes):
    trouve = t.resoudre_commune("Beziers-sur-Orb", "34500", index_communes)
    assert trouve["code_commune"] == "34032"
    assert trouve["rapprochement"] == "code_postal"


def test_resoudre_commune_echoue_explicitement(index_communes):
    """Une commune hors referentiel ne doit jamais etre rattachee au hasard.

    Le referentiel d'essai ne contient que 7 des 341 communes de l'Herault :
    Sete en est absente, et le rapprochement doit le dire au lieu de deviner.
    """
    trouve = t.resoudre_commune("Sète", "34200", index_communes)
    assert trouve["code_commune"] is None
    assert trouve["rapprochement"] == "echec"
    assert trouve["nom_commune"] == "Sète"


def test_code_postal_partage_ne_tranche_pas(index_communes):
    """34000 et 34070 pointent tous deux sur Montpellier ici, mais le test
    verifie qu'un code postal ambigu ne suffit pas a decider."""
    index = {
        "par_nom": {},
        "par_cp": {
            "34000": [
                {"code_commune": "34172", "nom_commune": "Mtp", "population": 1, "latitude": 0.0, "longitude": 0.0},
                {"code_commune": "34999", "nom_commune": "Autre", "population": 1, "latitude": 0.0, "longitude": 0.0},
            ]
        },
    }
    assert t.resoudre_commune("Inconnue", "34000", index)["code_commune"] is None


def test_indexer_communes_expose_le_centroide(index_communes):
    fiche = index_communes["par_nom"]["MONTPELLIER"]
    assert fiche["latitude"] == pytest.approx(43.61)
    assert fiche["longitude"] == pytest.approx(3.8742)


# ---------------------------------------------------------------------------
# Sections NAF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,lettre",
    [
        ("01.11Z", "A"),
        ("10.71C", "C"),
        ("43.32A", "F"),
        ("47.62Z", "G"),
        ("53.20Z", "H"),
        ("56.10C", "I"),
        ("68.20B", "L"),
        ("86.90D", "Q"),
        ("9900Z", "U"),
        ("5610C", "I"),  # forme sans point
        (None, "Z"),
        ("", "Z"),
        ("XX", "Z"),
        ("04.00Z", "Z"),  # division inexistante en NAF rev. 2
    ],
)
def test_section_naf(code, lettre):
    assert t.section_naf(code)[0] == lettre


def test_section_naf_renvoie_un_libelle_lisible():
    assert t.section_naf("56.10C")[1] == "Hebergement et restauration"


# ---------------------------------------------------------------------------
# Identite
# ---------------------------------------------------------------------------


def test_denomination_personne_morale(annonces):
    ligne = next(a for a in annonces if a["id"] == "B202601821470")
    assert t.denomination(ligne) == "SCI DES CHAMPS"


def test_denomination_personne_physique_prefere_le_nom_commercial(annonces):
    ligne = next(a for a in annonces if a["id"] == "B202601821459")
    assert t.denomination(ligne) == "lunette and co"


def test_denomination_replie_sur_le_champ_commercant():
    assert t.denomination({"commercant": "DUPONT, Jean", "listepersonnes": None}) == "DUPONT, Jean"


def test_charger_champ_json_tolere_les_deux_formes():
    assert t.charger_champ_json('{"a": 1}') == {"a": 1}
    assert t.charger_champ_json({"a": 1}) == {"a": 1}
    assert t.charger_champ_json("pas du json") is None
    assert t.charger_champ_json(None) is None


# ---------------------------------------------------------------------------
# Couche argent
# ---------------------------------------------------------------------------


def test_construire_argent_ecarte_les_annonces_republiees(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    assert len(annonces) == 10
    assert len(evenements) == 9
    assert len({e["id_annonce"] for e in evenements}) == 9


def test_construire_argent_garde_la_premiere_occurrence(annonces, index_communes, fiches):
    """La republication de l'annonce 1 est vide : c'est la version complete qui doit rester."""
    evenements = t.construire_argent(annonces, index_communes, fiches)
    creation = next(e for e in evenements if e["id_annonce"] == "A202601821317")
    assert creation["activite_declaree"] == "coursier, livraisons à domicile"


def test_enrichissement_pose_la_section_naf(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    creation = next(e for e in evenements if e["id_annonce"] == "A202601821317")
    assert creation["code_naf"] == "53.20Z"
    assert creation["section_naf"] == "H"


def test_sans_enrichissement_la_section_reste_inconnue(annonces, index_communes):
    evenements = t.construire_argent(annonces, index_communes, {})
    assert {e["section_naf"] for e in evenements} == {"Z"}


# ---------------------------------------------------------------------------
# Couche or
# ---------------------------------------------------------------------------


def test_solde_net_ignore_les_transferts(annonces, index_communes, fiches):
    """Une entreprise qui demenage ne cree ni ne detruit d'activite."""
    evenements = t.construire_argent(annonces, index_communes, fiches)
    lignes = t.agreger_par_commune_secteur(evenements)
    transferts = sum(ligne["nb_transferts"] for ligne in lignes)
    assert transferts == 2
    # 1 creation, 0 immatriculation nette, 1 radiation, 1 liquidation.
    assert sum(ligne["solde_net"] for ligne in lignes) == 1 - 1 - 1


def test_agregation_conserve_tous_les_evenements(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    lignes = t.agreger_par_commune_secteur(evenements)
    assert sum(ligne["nb_evenements"] for ligne in lignes) == len(evenements)


def test_agregation_grain_unique(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    lignes = t.agreger_par_commune_secteur(evenements)
    cles = [(x["date_parution"], x["code_commune"], x["section_naf"]) for x in lignes]
    assert len(cles) == len(set(cles))


def test_defaillances_comptees_une_seule_fois(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    lignes = t.agreger_par_commune_secteur(evenements)
    assert sum(ligne["nb_defaillances"] for ligne in lignes) == 2
    assert sum(ligne["nb_liquidations"] for ligne in lignes) == 1
    assert sum(ligne["nb_redressements"] for ligne in lignes) == 1


def test_montant_de_vente_cumule(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    lignes = t.agreger_par_commune_secteur(evenements)
    total = sum(ligne["montant_ventes_eur"] or 0 for ligne in lignes)
    assert total == 345000.00


def test_signaux_prioritaires_tries_par_gravite(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    signaux = t.signaux_prioritaires(evenements)
    # Gravite egale (3) pour les deux : c'est alors la commune qui tranche,
    # Agde avant Montpellier. Le tri doit etre deterministe.
    assert [s["type_evenement"] for s in signaux] == [t.REDRESSEMENT, t.LIQUIDATION]
    assert all(s["gravite"] >= 2 for s in signaux)


def test_synthese_journaliere(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    lignes = t.agreger_par_commune_secteur(evenements)
    synthese = t.synthese_journaliere(evenements, lignes)
    assert synthese["nb_evenements"] == 9
    assert synthese["nb_defaillances"] == 2
    assert synthese["nb_avec_siren"] == 9
    assert synthese["nb_sans_commune"] == 0
    assert synthese["taux_rapprochement_commune"] == 1.0
    assert synthese["nb_communes_touchees"] == 7


def test_fenetre_glissante_cumule_les_jours(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    jour1 = t.agreger_par_commune_secteur(evenements)
    jour2 = [dict(ligne, date_parution="2026-09-22") for ligne in jour1]
    cumul = t.fenetre_glissante(jour1 + jour2, "2026-09-23", jours=30)
    assert sum(ligne["nb_evenements"] for ligne in cumul) == 2 * len(evenements)


def test_fenetre_glissante_exclut_hors_periode(annonces, index_communes, fiches):
    evenements = t.construire_argent(annonces, index_communes, fiches)
    recent = t.agreger_par_commune_secteur(evenements)
    ancien = [dict(ligne, date_parution="2026-01-05") for ligne in recent]
    cumul = t.fenetre_glissante(recent + ancien, "2026-09-23", jours=30)
    assert sum(ligne["nb_evenements"] for ligne in cumul) == len(evenements)
