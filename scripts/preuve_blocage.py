"""Preuve, dans le run d'integration continue, que les controles bloquent vraiment.

Un controle de qualite qui ne fait jamais echouer personne n'est pas un
controle, c'est un commentaire. Ce script force trois situations anormales et
exige que chacune leve. Il sort en code 0 seulement si les trois ont bloque.

Sortie conservee dans `results/preuve_blocage.txt`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radar.quality import (  # noqa: E402
    QualiteError,
    controle_conservation,
    controle_fraicheur,
    controle_unicite,
    controle_volumetrie,
    exiger,
)

EVENEMENT = {"id_annonce": "A1", "date_parution": "2026-09-22", "type_evenement": "creation", "code_commune": "34172"}

CAS = [
    (
        "volumetrie effondree",
        [controle_volumetrie(0, [80, 90, 85, 88, 92, 87])],
    ),
    (
        "source gelee depuis sept semaines",
        [controle_fraicheur("2026-08-01", "2026-09-22")],
    ),
    (
        "annonce publiee deux fois",
        [controle_unicite([EVENEMENT, dict(EVENEMENT)])],
    ),
    (
        "evenements perdus entre argent et or",
        [controle_conservation([EVENEMENT, dict(EVENEMENT, id_annonce="A2")], [{"nb_evenements": 1}])],
    ),
]


def main() -> int:
    bloques = 0
    for nom, resultats in CAS:
        try:
            exiger(resultats, nom)
        except QualiteError as erreur:
            bloques += 1
            print(f"BLOCAGE CONFIRME [{nom}] : {erreur}\n")
        else:
            print(f"ECHEC DE LA PREUVE [{nom}] : le controle a laisse passer\n")
    print(f"{bloques} cas sur {len(CAS)} ont bien arrete le graphe.")
    return 0 if bloques == len(CAS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
