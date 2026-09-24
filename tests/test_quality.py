"""Tests des controles de qualite.

On verifie les deux sens : un controle passe quand il doit passer, et il echoue
quand il doit echouer. Un controle qui ne sait pas echouer ne controle rien.
"""

from __future__ import annotations

import pytest

from radar import quality as q
from radar import transform as t


@pytest.fixture
def evenements(annonces, index_communes, fiches):
    return t.construire_argent(annonces, index_communes, fiches)


@pytest.fixture
def lignes_or(evenements):
    return t.agreger_par_commune_secteur(evenements)


# ---------------------------------------------------------------------------
# Couche argent
# ---------------------------------------------------------------------------


def test_unicite_passe_sur_une_partition_saine(evenements):
    assert q.controle_unicite(evenements).reussi


def test_unicite_detecte_un_doublon(evenements):
    assert not q.controle_unicite([*evenements, evenements[0]]).reussi


def test_valeurs_attendues_detecte_un_type_inconnu(evenements):
    pollue = [*evenements, dict(evenements[0], type_evenement="chose_inconnue")]
    resultat = q.controle_valeurs_attendues(pollue)
    assert not resultat.reussi
    assert resultat.detail["inconnus"] == ["chose_inconnue"]


def test_partition_homogene(evenements, jour):
    assert q.controle_date_logique(evenements, jour).reussi
    melange = [*evenements, dict(evenements[0], id_annonce="Z", date_parution="2026-01-01")]
    assert not q.controle_date_logique(melange, jour).reussi


def test_perimetre_departemental(evenements):
    assert q.controle_perimetre(evenements, "34").reussi
    hors = [*evenements, dict(evenements[0], id_annonce="Z", code_commune="30001")]
    assert not q.controle_perimetre(hors, "34").reussi


def test_cles_non_nulles(evenements):
    assert q.controle_identifiants_non_nuls(evenements).reussi
    assert not q.controle_identifiants_non_nuls([*evenements, dict(evenements[0], id_annonce=None)]).reussi


# ---------------------------------------------------------------------------
# Volumetrie
# ---------------------------------------------------------------------------


# Distribution reelle des 60 jours de parution du BODACC pour l'Herault,
# du 24/06/2026 au 21/09/2026. Elle sert de reference aux tests parce qu'un
# controle calibre sur des nombres inventes ne prouve rien.
HISTORIQUE_REEL = [
    1, 4, 9, 11, 11, 60, 77, 93, 101, 164, 181, 184, 218, 219, 222, 243, 278, 303, 304, 324,
    324, 342, 354, 356, 357, 361, 365, 389, 401, 423, 427, 428, 438, 439, 440, 444, 464, 468,
    471, 478, 495, 495, 512, 514, 525, 545, 565, 574, 580, 598, 634, 710, 723, 808, 820, 899,
    950, 1003, 1012, 1046,
]


def test_volumetrie_accepte_les_journees_creuses_reelles():
    """Une journee a 79 ou 101 annonces est normale : le BODACC ne publie pas
    les memes familles d'avis tous les jours. Un seuil a la mediane sur quatre
    les refusait toutes les deux, a tort."""
    assert q.controle_volumetrie(101, HISTORIQUE_REEL).reussi
    assert q.controle_volumetrie(79, HISTORIQUE_REEL).reussi
    assert q.controle_volumetrie(425, HISTORIQUE_REEL).reussi
    assert q.controle_volumetrie(1046, HISTORIQUE_REEL).reussi


def test_volumetrie_refuse_une_journee_hors_de_tout_l_historique():
    assert not q.controle_volumetrie(0, HISTORIQUE_REEL).reussi
    assert not q.controle_volumetrie(3, HISTORIQUE_REEL).reussi
    assert not q.controle_volumetrie(5000, HISTORIQUE_REEL).reussi


def test_volumetrie_publie_les_bornes_et_leur_origine():
    resultat = q.controle_volumetrie(425, HISTORIQUE_REEL)
    assert resultat.detail["jours_historique"] == 60
    assert resultat.detail["borne_basse"] < resultat.detail["centile_5"]
    assert resultat.detail["borne_haute"] > resultat.detail["centile_95"]
    assert "centiles 5 et 95" in resultat.attendu


def test_volumetrie_exige_au_moins_une_annonce_sans_historique():
    assert not q.controle_volumetrie(0, []).reussi
    assert q.controle_volumetrie(12, []).reussi


def test_volumetrie_ignore_les_jours_sans_parution():
    """Le BODACC ne parait ni le dimanche ni le lundi : les zeros ne sont pas
    des jours de parution et ne doivent pas entrer dans la distribution."""
    resultat = q.controle_volumetrie(425, [0, 0, *HISTORIQUE_REEL])
    assert resultat.reussi
    assert resultat.detail["jours_historique"] == 60


# ---------------------------------------------------------------------------
# Completude de l'extraction
# ---------------------------------------------------------------------------


def test_completude_extraction():
    assert q.controle_completude_extraction(475, 475).reussi
    resultat = q.controle_completude_extraction(400, 475)
    assert not resultat.reussi
    assert resultat.detail["ecart"] == -75


def test_completude_sans_total_connu_est_un_echec():
    """Ne pas savoir combien la source annonce n'est pas une raison de publier."""
    assert not q.controle_completude_extraction(475, None).reussi


# ---------------------------------------------------------------------------
# Fraicheur
# ---------------------------------------------------------------------------


def test_fraicheur_ok_quand_la_source_publie():
    assert q.controle_fraicheur("2026-09-23", "2026-09-23").reussi
    assert q.controle_fraicheur("2026-09-20", "2026-09-23").reussi


def test_fraicheur_echoue_quand_la_source_s_arrete():
    resultat = q.controle_fraicheur("2026-08-01", "2026-09-23")
    assert not resultat.reussi
    assert resultat.detail["ecart_jours"] == 53


def test_fraicheur_echoue_si_la_source_ne_repond_rien():
    assert not q.controle_fraicheur(None, "2026-09-23").reussi


# ---------------------------------------------------------------------------
# Rapprochement
# ---------------------------------------------------------------------------


def test_rapprochement_communes_au_dessus_du_seuil(evenements):
    resultat = q.controle_rapprochement_communes(evenements)
    assert resultat.reussi
    assert resultat.detail["taux"] == 1.0


def test_rapprochement_communes_sous_le_seuil(evenements):
    """Deux annonces sur neuf sans commune : 77,8 %, sous le seuil de 95 %."""
    degrades = [dict(e) for e in evenements]
    degrades[0]["code_commune"] = None
    degrades[1]["code_commune"] = None
    resultat = q.controle_rapprochement_communes(degrades)
    assert not resultat.reussi
    assert resultat.detail["taux"] == pytest.approx(7 / 9, abs=1e-4)


def test_presence_siren_est_une_alerte_pas_un_blocage(evenements):
    sans = [dict(e, siren=None) for e in evenements]
    resultat = q.controle_siren(sans)
    assert not resultat.reussi
    assert resultat.bloquant is False
    assert resultat.statut == "ALERTE"


# ---------------------------------------------------------------------------
# Couche or
# ---------------------------------------------------------------------------


def test_conservation_argent_vers_or(evenements, lignes_or):
    assert q.controle_conservation(evenements, lignes_or).reussi
    assert not q.controle_conservation(evenements, lignes_or[:-1]).reussi


def test_grain_or_unique(lignes_or):
    assert q.controle_grain_or(lignes_or).reussi
    assert not q.controle_grain_or([*lignes_or, lignes_or[0]]).reussi


def test_coherence_solde(lignes_or):
    assert q.controle_coherence_solde(lignes_or).reussi
    faux = [dict(lignes_or[0], solde_net=99), *lignes_or[1:]]
    assert not q.controle_coherence_solde(faux).reussi


def test_compteurs_positifs(lignes_or):
    assert q.controle_bornes_or(lignes_or).reussi
    assert not q.controle_bornes_or([dict(lignes_or[0], nb_creations=-1)]).reussi


def test_couverture_naf_non_bloquante(lignes_or):
    resultat = q.controle_couverture_naf([dict(ligne, section_naf="Z") for ligne in lignes_or])
    assert not resultat.reussi
    assert resultat.bloquant is False


# ---------------------------------------------------------------------------
# Rendu et blocage
# ---------------------------------------------------------------------------


def test_exiger_leve_sur_un_echec_bloquant(evenements, jour):
    resultats = [q.controle_unicite([*evenements, evenements[0]])]
    with pytest.raises(q.QualiteError) as erreur:
        q.exiger(resultats, "essai")
    assert "unicite_id_annonce" in str(erreur.value)


def test_exiger_laisse_passer_une_alerte(evenements):
    resultats = [q.controle_siren([dict(e, siren=None) for e in evenements])]
    rapport = q.exiger(resultats, "essai")
    assert rapport["alertes"] == 1
    assert rapport["echecs_bloquants"] == 0


def test_tableau_est_lisible(evenements, jour):
    resultats = q.controler_silver(evenements, jour, "34", HISTORIQUE_REEL, "2026-09-23", 9, 9)
    tableau = q.rendre_tableau(resultats)
    assert "unicite_id_annonce" in tableau
    assert tableau.count("\n") >= len(resultats)
    assert "controles" in tableau


def test_enchainement_gold_complet(evenements, lignes_or):
    resultats = q.controler_gold(evenements, lignes_or)
    assert len(resultats) == 5
    assert all(r.reussi for r in resultats if r.bloquant)
