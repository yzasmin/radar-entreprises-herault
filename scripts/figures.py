"""Graphiques du README, de la fiche et du teaser.

Thème sombre du portfolio : fond #0D0F12, texte #ECE8DF, accent engineering
#F2B84B. Les donnees viennent uniquement de `results/`, jamais d'un calcul
refait ici : une figure ne doit pas pouvoir afficher un chiffre absent du dossier.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RACINE = Path(__file__).resolve().parents[1]
RESULTATS = RACINE / "results"
FIGURES = RESULTATS / "figures"

FOND = "#0D0F12"
TEXTE = "#ECE8DF"
ACCENT = "#F2B84B"
ROUGE = "#E5674E"
VERT = "#5BC48A"
GRILLE = "#2A2F36"


def _style():
    plt.rcParams.update(
        {
            "figure.facecolor": FOND,
            "axes.facecolor": FOND,
            "savefig.facecolor": FOND,
            "text.color": TEXTE,
            "axes.labelcolor": TEXTE,
            "xtick.color": TEXTE,
            "ytick.color": TEXTE,
            "axes.edgecolor": GRILLE,
            "grid.color": GRILLE,
            "font.size": 11,
            "axes.titlesize": 14,
            "figure.dpi": 100,
        }
    )


def _lire_csv(nom: str) -> list[dict]:
    chemin = RESULTATS / nom
    if not chemin.exists():
        return []
    with chemin.open(encoding="utf-8", newline="") as fichier:
        return list(csv.DictReader(fichier))


def _entier(valeur) -> int:
    try:
        return int(float(valeur))
    except (TypeError, ValueError):
        return 0


def figure_communes(jour: str, chemin: Path, haut: int = 12):
    """Barres horizontales : les communes qui bougent le plus ce jour-la."""
    lignes = _lire_csv("requete_top_communes.csv")[:haut]
    if not lignes:
        return None
    lignes = list(reversed(lignes))
    noms = [ligne["nom_commune"] for ligne in lignes]
    creations = [_entier(ligne["creations"]) for ligne in lignes]
    radiations = [_entier(ligne["radiations"]) for ligne in lignes]
    defaillances = [_entier(ligne["defaillances"]) for ligne in lignes]
    autres = [
        _entier(ligne["evenements"]) - c - r - d
        for ligne, c, r, d in zip(lignes, creations, radiations, defaillances, strict=True)
    ]

    _style()
    figure, axe = plt.subplots(figsize=(16, 9))
    position = range(len(noms))
    gauche = [0] * len(noms)
    for valeurs, couleur, etiquette in (
        (creations, VERT, "Creations et immatriculations"),
        (radiations, "#8A93A0", "Radiations"),
        (defaillances, ROUGE, "Defaillances"),
        (autres, ACCENT, "Autres evenements"),
    ):
        axe.barh(list(position), valeurs, left=gauche, color=couleur, label=etiquette, height=0.68)
        gauche = [g + v for g, v in zip(gauche, valeurs, strict=True)]
    axe.set_yticks(list(position))
    axe.set_yticklabels(noms)
    axe.set_xlabel("Annonces publiees au BODACC")
    axe.set_title(f"Herault, parution du {jour} : {haut} communes les plus actives", color=TEXTE, pad=16)
    axe.grid(axis="x", alpha=0.35)
    axe.spines[["top", "right"]].set_visible(False)
    legende = axe.legend(loc="lower right", frameon=False)
    for texte in legende.get_texts():
        texte.set_color(TEXTE)
    figure.tight_layout()
    figure.savefig(chemin, facecolor=FOND)
    plt.close(figure)
    return chemin


def figure_sections(jour: str, chemin: Path):
    """Solde net par section NAF : ou l'economie locale ouvre, ou elle ferme."""
    lignes = [ligne for ligne in _lire_csv("requete_par_section.csv") if ligne["section_naf"] != "Z"]
    if not lignes:
        return None
    lignes.sort(key=lambda ligne: _entier(ligne["evenements"]), reverse=True)
    lignes = lignes[:10][::-1]
    etiquettes = [f"{ligne['section_naf']} - {ligne['libelle_section_naf'][:38]}" for ligne in lignes]
    soldes = [_entier(ligne["solde_net"]) for ligne in lignes]
    couleurs = [VERT if valeur > 0 else (ROUGE if valeur < 0 else "#8A93A0") for valeur in soldes]

    _style()
    figure, axe = plt.subplots(figsize=(16, 9))
    axe.barh(range(len(etiquettes)), soldes, color=couleurs, height=0.66)
    axe.set_yticks(range(len(etiquettes)))
    axe.set_yticklabels(etiquettes)
    axe.axvline(0, color=TEXTE, linewidth=1)
    axe.set_xlabel("Solde net : ouvertures moins radiations et liquidations")
    axe.set_title(f"Solde net par secteur, parution du {jour}", color=TEXTE, pad=16)
    axe.grid(axis="x", alpha=0.35)
    axe.spines[["top", "right"]].set_visible(False)
    for index, valeur in enumerate(soldes):
        decalage = 0.08 if valeur >= 0 else -0.08
        axe.text(
            valeur + decalage,
            index,
            f"{valeur:+d}",
            va="center",
            ha="left" if valeur >= 0 else "right",
            color=TEXTE,
            fontsize=10,
        )
    figure.tight_layout()
    figure.savefig(chemin, facecolor=FOND)
    plt.close(figure)
    return chemin


def figure_controles(chemin: Path):
    """Les controles de qualite, tels qu'ils sont ecrits dans results/."""
    lignes = _lire_csv("controles_qualite.csv")
    if not lignes:
        return None
    _style()
    figure, axe = plt.subplots(figsize=(16, 9))
    axe.axis("off")
    entetes = ["Etape", "Controle", "Bloquant", "Statut", "Observe"]
    corps = [
        [
            ligne["etape"],
            ligne["controle"],
            "oui" if ligne["bloquant"].lower() in {"true", "oui", "1"} else "non",
            ligne["statut"],
            ligne["observe"][:52],
        ]
        for ligne in lignes
    ]
    table = axe.table(cellText=corps, colLabels=entetes, loc="center", cellLoc="left", colWidths=[0.08, 0.26, 0.09, 0.09, 0.48])
    table.auto_set_font_size(False)
    table.set_fontsize(9.5)
    table.scale(1, 1.55)
    for (rangee, _colonne), cellule in table.get_celld().items():
        cellule.set_edgecolor(GRILLE)
        if rangee == 0:
            cellule.set_facecolor("#171B21")
            cellule.set_text_props(color=ACCENT, weight="bold")
        else:
            cellule.set_facecolor(FOND)
            statut = corps[rangee - 1][3]
            cellule.set_text_props(color=VERT if statut == "OK" else (ROUGE if statut == "ECHEC" else ACCENT))
    axe.set_title("Controles de qualite du graphe, sortie reelle du run", color=TEXTE, pad=24)
    figure.tight_layout()
    figure.savefig(chemin, facecolor=FOND)
    plt.close(figure)
    return chemin


def figure_signaux(jour: str, chemin: Path, haut: int = 14):
    """Les signaux du jour : procedures collectives, par commune."""
    lignes = _lire_csv("requete_signaux_du_jour.csv")
    if not lignes:
        return None
    from collections import Counter

    compteur = Counter((ligne["nom_commune"] or "commune inconnue") for ligne in lignes)
    communes = compteur.most_common(haut)[::-1]
    _style()
    figure, axe = plt.subplots(figsize=(16, 9))
    axe.barh([c for c, _ in communes], [n for _, n in communes], color=ROUGE, height=0.62)
    axe.set_xlabel("Procedures collectives publiees")
    axe.set_title(f"Signaux de risque du {jour} : {len(lignes)} procedures collectives", color=TEXTE, pad=16)
    axe.grid(axis="x", alpha=0.35)
    axe.spines[["top", "right"]].set_visible(False)
    axe.xaxis.get_major_locator().set_params(integer=True)
    figure.tight_layout()
    figure.savefig(chemin, facecolor=FOND)
    plt.close(figure)
    return chemin


def main() -> int:
    parseur = argparse.ArgumentParser()
    parseur.add_argument("--jour", required=True)
    parseur.add_argument("--teaser", default=str(RACINE / "teaser" / "figure.png"))
    args = parseur.parse_args()
    FIGURES.mkdir(parents=True, exist_ok=True)

    produites = []
    for nom, fonction in (
        ("communes.png", lambda chemin: figure_communes(args.jour, chemin)),
        ("secteurs.png", lambda chemin: figure_sections(args.jour, chemin)),
        ("controles-qualite.png", figure_controles),
        ("signaux.png", lambda chemin: figure_signaux(args.jour, chemin)),
    ):
        resultat = fonction(FIGURES / nom)
        if resultat:
            produites.append(nom)

    # Le teaser reprend la figure des communes, en 1600x900.
    teaser = Path(args.teaser)
    teaser.parent.mkdir(parents=True, exist_ok=True)
    figure_communes(args.jour, teaser)

    print(json.dumps({"figures": produites, "teaser": str(teaser)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
