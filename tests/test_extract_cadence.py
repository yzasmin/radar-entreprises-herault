"""Tests du limiteur de debit et de l'enrichissement parallele.

L'API Recherche d'entreprises accepte 7 requetes par seconde et par adresse IP.
Depasser cette limite, c'est se faire couper l'acces ; rester trop en dessous,
c'est transformer 500 appels en huit minutes d'attente. Ces tests verifient les
deux bornes.
"""

from __future__ import annotations

import time

import pytest

from radar import extract
from radar.config import Config
from radar.extract import Cadence


def test_la_cadence_respecte_l_intervalle():
    cadence = Cadence(20)  # 50 ms entre deux jetons
    debut = time.monotonic()
    for _ in range(6):
        cadence.attendre()
    ecoule = time.monotonic() - debut
    # 6 jetons a 20 par seconde : au moins 5 intervalles de 50 ms.
    assert ecoule >= 0.25
    assert ecoule < 1.0


def test_la_cadence_est_partagee_entre_les_fils():
    from concurrent.futures import ThreadPoolExecutor

    cadence = Cadence(20)
    debut = time.monotonic()
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: cadence.attendre(), range(6)))
    ecoule = time.monotonic() - debut
    # Meme en parallele, le debit global reste borne : 6 jetons, 5 intervalles.
    assert ecoule >= 0.25


def test_le_premier_jeton_ne_fait_pas_attendre():
    assert Cadence(2).attendre() == 0.0


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("RADAR_RPS", "50")
    monkeypatch.setenv("RADAR_FILS", "4")
    monkeypatch.setenv("RADAR_MAX_SIRENS", "3")
    return Config()


def test_enrichissement_respecte_le_plafond_de_sirens(cfg, monkeypatch):
    appels: list[str] = []

    def faux_get(url, parametres, essais=4, delai=2.0):
        appels.append(parametres["q"])
        return {"results": [{"siren": parametres["q"], "activite_principale": "56.10C"}]}

    monkeypatch.setattr(extract, "_get", faux_get)
    fiches = extract.enrichir_sirens(cfg, ["109482182", "821553419", "849933825", "107129215"])
    assert len(appels) == 3, "RADAR_MAX_SIRENS doit borner le nombre d'appels"
    assert set(fiches) == {"109482182", "821553419", "849933825"}


def test_un_siren_introuvable_n_est_pas_une_erreur(cfg, monkeypatch):
    def faux_get(url, parametres, essais=4, delai=2.0):
        if parametres["q"] == "821553419":
            return {"results": []}
        return {"results": [{"siren": parametres["q"]}]}

    monkeypatch.setattr(extract, "_get", faux_get)
    fiches = extract.enrichir_sirens(cfg, ["109482182", "821553419"])
    assert set(fiches) == {"109482182"}


def test_une_panne_sur_un_siren_ne_perd_pas_les_autres(cfg, monkeypatch):
    def faux_get(url, parametres, essais=4, delai=2.0):
        if parametres["q"] == "821553419":
            raise extract.ExtractionError("503")
        return {"results": [{"siren": parametres["q"]}]}

    monkeypatch.setattr(extract, "_get", faux_get)
    fiches = extract.enrichir_sirens(cfg, ["109482182", "821553419", "849933825"])
    assert set(fiches) == {"109482182", "849933825"}


def test_une_reponse_qui_ne_correspond_pas_au_siren_demande_est_ignoree(cfg, monkeypatch):
    """La recherche plein texte peut renvoyer une autre entreprise : on verifie le SIREN."""

    def faux_get(url, parametres, essais=4, delai=2.0):
        return {"results": [{"siren": "999999999", "nom_complet": "AUTRE CHOSE"}]}

    monkeypatch.setattr(extract, "_get", faux_get)
    assert extract.enrichir_sirens(cfg, ["109482182"]) == {}


def test_liste_vide(cfg):
    assert extract.enrichir_sirens(cfg, []) == {}
