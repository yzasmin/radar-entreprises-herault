"""Verifie que chaque nombre publie existe bien dans `results/`.

La regle du portfolio est simple : aucun chiffre affiche dans un README ou dans
une fiche ne doit etre introuvable dans une sortie d'execution. Ce script la
rend verifiable en une commande au lieu d'une relecture a l'oeil.

    python scripts/verifier_chiffres.py README.md
    python scripts/verifier_chiffres.py README.md ../../site/src/content/projets/07-radar-entreprises.md

Methode : on extrait tous les nombres du document, on construit l'ensemble de
tous les nombres presents dans `results/` (JSON a plat, en-tetes et cellules de
CSV, sorties texte), et on liste ceux du document qui n'y figurent pas.
Le script ne remplace pas une relecture : il attrape les chiffres oublies, pas
les chiffres justes places au mauvais endroit.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
RESULTATS = RACINE / "results"

# Nombres qui ne viennent pas d'une execution : versions, dates, ports, identifiants.
IGNORES = {
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "12",
    "15",
    "16",
    "20",
    "24",
    "30",
    "60",
    "64",
    "90",
    "100",
    "2024",
    "2026",
    "4566",
    "8080",
    "5432",
    "50000",
    "10000",
}

MOTIF_NOMBRE = re.compile(r"(?<![\w.\-/])(\d[\d\s ]*(?:[.,]\d+)?)(?![\w/])")


def _normaliser(brut: str) -> str:
    """« 1 374 086,0 » et « 1374086.00 » doivent se comparer."""
    texte = re.sub(r"[\s ]", "", brut).replace(",", ".")
    if "." in texte:
        texte = texte.rstrip("0").rstrip(".")
    return texte or "0"


def _nombres(texte: str) -> set[str]:
    return {_normaliser(m.group(1)) for m in MOTIF_NOMBRE.finditer(texte)}


def _aplatir(valeur, sortie: set[str]) -> None:
    if isinstance(valeur, dict):
        for cle, sous in valeur.items():
            sortie |= _nombres(str(cle))
            _aplatir(sous, sortie)
    elif isinstance(valeur, list):
        for sous in valeur:
            _aplatir(sous, sortie)
    elif isinstance(valeur, bool):
        return
    elif isinstance(valeur, int | float):
        sortie.add(_normaliser(str(valeur)))
        # Les pourcentages sont publies en pour-cent, les fichiers en fraction.
        if isinstance(valeur, float) and 0 < valeur < 1:
            sortie.add(_normaliser(f"{valeur * 100:.2f}"))
            sortie.add(_normaliser(f"{valeur * 100:.1f}"))
    elif valeur is not None:
        sortie |= _nombres(str(valeur))


def nombres_des_resultats() -> set[str]:
    trouves: set[str] = set()
    if not RESULTATS.exists():
        return trouves
    for chemin in sorted(RESULTATS.rglob("*")):
        if chemin.is_dir():
            continue
        if chemin.suffix == ".json":
            try:
                _aplatir(json.loads(chemin.read_text(encoding="utf-8")), trouves)
            except (ValueError, UnicodeDecodeError):
                continue
        elif chemin.suffix == ".csv":
            try:
                with chemin.open(encoding="utf-8", newline="") as fichier:
                    for ligne in csv.reader(fichier):
                        for cellule in ligne:
                            trouves |= _nombres(cellule)
            except (UnicodeDecodeError, csv.Error):
                continue
        elif chemin.suffix in {".txt", ".log", ".md"}:
            try:
                trouves |= _nombres(chemin.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    # Les totaux derives publies dans les documents : sommes et differences simples
    # ne sont pas recalculees ici, ils doivent apparaitre dans un fichier.
    return trouves


def verifier(document: Path, disponibles: set[str]) -> list[str]:
    texte = document.read_text(encoding="utf-8")
    # On retire les blocs de code : ils contiennent des versions et des commandes.
    texte = re.sub(r"```.*?```", "", texte, flags=re.S)
    texte = re.sub(r"`[^`]*`", "", texte)
    # Les liens et les dates ne sont pas des mesures.
    texte = re.sub(r"https?://\S+", "", texte)
    texte = re.sub(r"\d{4}-\d{2}-\d{2}", "", texte)
    texte = re.sub(r"\d{1,2}/\d{1,2}/\d{4}", "", texte)
    manquants = []
    for nombre in sorted(_nombres(texte) - IGNORES):
        if nombre not in disponibles:
            manquants.append(nombre)
    return manquants


def main(argv: list[str]) -> int:
    documents = [Path(a) for a in argv[1:]] or [RACINE / "README.md"]
    disponibles = nombres_des_resultats()
    print(f"{len(disponibles)} nombres distincts trouves dans results/\n")
    total = 0
    for document in documents:
        if not document.exists():
            print(f"ABSENT : {document}")
            total += 1
            continue
        manquants = verifier(document, disponibles)
        etat = "OK" if not manquants else f"{len(manquants)} nombre(s) introuvables"
        print(f"{etat:>28}  {document}")
        for nombre in manquants:
            print(f"      - {nombre}")
        total += len(manquants)
    print()
    if total:
        print("Chaque nombre ci-dessus doit soit venir d'une execution, soit etre retire.")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
