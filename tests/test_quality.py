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


def test_volumetrie_calibree_sur_la_mediane():
    historique = [50, 55, 60, 58, 62, 57, 61, 59]
    assert q.controle_volumetrie(58, historique).reussi
    assert q.controle_volumetrie(20, historique).reussi  # 20 > 59/4
    assert not q.controle_volumetrie(3, historique).reussi  # effondrement
    assert not q.controle_volumetrie(500, historique).reussi  # explosion


def test_volumetrie_exige_au_moins_une_annonce_sans_historique():
    assert not q.controle_volumetrie(0, []).reussi
    assert q.controle_volumetrie(12, []).reussi


def test_volumetrie_ignore_les_jours_sans_parution():
    """Le BODACC ne parait pas le dimanche : les zeros ne doivent pas tirer la mediane."""
    resultat = q.controle_volumetrie(55, [0, 0, 50, 55, 60, 58, 62, 57])
    assert resultat.reussi
    assert resultat.detail["jours_historique"] == 6


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
    resultats = q.controler_argent(evenements, jour, "34", [50, 55, 60, 58, 62, 57], "2026-09-23")
    tableau = q.rendre_tableau(resultats)
    assert "unicite_id_annonce" in tableau
    assert tableau.count("\n") >= len(resultats)
    assert "controles" in tableau


def test_enchainement_or_complet(evenements, lignes_or):
    resultats = q.controler_or(evenements, lignes_or)
    assert len(resultats) == 5
    assert all(r.reussi for r in resultats if r.bloquant)
