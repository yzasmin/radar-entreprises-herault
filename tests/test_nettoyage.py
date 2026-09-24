"""Tests de la commande de nettoyage.

Supprimer est la seule operation irreversible du projet : elle merite plus de
tests que les autres, et un garde-fou qui refuse tout prefixe hors du projet.
"""

from __future__ import annotations

import pytest

pytest.importorskip("moto")
from moto import mock_aws

from radar.config import Config
from radar.storage import assurer_seau, ecrire_json, lister, supprimer_prefixe


@pytest.fixture
def cfg(monkeypatch):
    for nom, valeur in {
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_SESSION_TOKEN": "test",
        "AWS_REGION": "eu-north-1",
        "RADAR_BUCKET": "radar-test",
        "RADAR_PREFIXE": "radar",
    }.items():
        monkeypatch.setenv(nom, valeur)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    return Config()


def _remplir(cfg):
    assurer_seau(cfg)
    ecrire_json(cfg, {"a": 1}, "radar/bronze/bodacc/date_parution=2026-09-22/annonces.json")
    ecrire_json(cfg, {"a": 1}, "radar/bronze/bodacc/date_parution=2026-09-23/annonces.json")
    ecrire_json(cfg, {"a": 1}, "radar/silver/evenements/date_parution=2026-09-22/x.json")
    ecrire_json(cfg, {"a": 1}, "autre-projet/donnees.json")


@mock_aws
def test_sans_confirmation_rien_n_est_supprime(cfg):
    _remplir(cfg)
    rapport = supprimer_prefixe(cfg, "radar")
    assert rapport["nb_objets"] == 3
    assert rapport["supprime"] is False
    assert len(lister(cfg, "radar")) == 3


@mock_aws
def test_suppression_de_tout_le_prefixe_du_projet(cfg):
    _remplir(cfg)
    rapport = supprimer_prefixe(cfg, "radar", confirmer=True)
    assert rapport["supprime"] is True
    assert lister(cfg, "radar") == []
    # Ce qui n'appartient pas au projet n'est pas touche.
    assert len(lister(cfg, "autre-projet")) == 1


@mock_aws
def test_suppression_d_une_seule_couche(cfg):
    _remplir(cfg)
    supprimer_prefixe(cfg, "radar/silver", confirmer=True)
    assert lister(cfg, "radar/silver") == []
    assert len(lister(cfg, "radar/bronze")) == 2


@mock_aws
def test_suppression_d_une_seule_partition(cfg):
    _remplir(cfg)
    supprimer_prefixe(cfg, "radar", confirmer=True, filtre_jour="2026-09-23")
    restants = [o["cle"] for o in lister(cfg, "radar")]
    assert all("2026-09-23" not in cle for cle in restants)
    assert len(restants) == 2


@mock_aws
def test_un_prefixe_hors_du_projet_est_refuse(cfg):
    _remplir(cfg)
    with pytest.raises(ValueError, match="prefixe refuse"):
        supprimer_prefixe(cfg, "", confirmer=True)
    with pytest.raises(ValueError, match="prefixe refuse"):
        supprimer_prefixe(cfg, "autre-projet", confirmer=True)
    assert len(lister(cfg, "autre-projet")) == 1
