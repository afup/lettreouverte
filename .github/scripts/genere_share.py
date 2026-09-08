#!/usr/bin/env python3
"""Régénère `assets/share.png` à partir du gabarit `assets/share-generique.png`.

Le gabarit est la carte de partage vidée de ses deux occurrences du nombre de
signataires : le rond blanc et la deuxième ligne du paragraphe. Ce script y
réinscrit le nombre, en Open Sans Bold, aux mêmes emplacements que la carte
d'origine.

Par défaut le nombre est lu dans `index.html` (`span.signatories__count`), de
sorte que la carte ne puisse pas diverger de la grille des signataires.

Utilisation :
    python3 .github/scripts/genere_share.py            # nombre lu dans index.html
    python3 .github/scripts/genere_share.py --nombre 34
    python3 .github/scripts/genere_share.py --nombre 34 --sortie /tmp/apercu.png

Dépendance : Pillow (`pip install pillow`).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - dépend de l'environnement
    sys.exit("Pillow est requis : pip install pillow")

RACINE = Path(__file__).resolve().parents[2]
GABARIT = RACINE / "assets" / "share-generique.png"
SORTIE = RACINE / "assets" / "share.png"
PAGE = RACINE / "index.html"
POLICE = RACINE / "assets" / "fonts" / "Open_Sans" / "static" / "OpenSans-Bold.ttf"

RE_COUNT = re.compile(r'<span class="signatories__count">\s*([0-9]+)\s*</span>')

ORANGE = (255, 145, 77)
BLANC = (255, 255, 255)

# Repères relevés sur la carte d'origine (1200x630).
ROND_CENTRE = (199, 223)  # centre du disque blanc
ROND_TAILLE = 140
# Le gabarit réserve un creux avant « assos » : le nombre y est calé par la
# droite, l'espace-mot qui suit restant ainsi constant quel que soit le nombre
# de chiffres. Un nombre à trois chiffres débordera un peu dans la marge, comme
# un chiffre en drapeau — c'est le seul degré de liberté que laisse le gabarit.
LIGNE_DROITE = 444  # bord droit du nombre, un espace-mot avant « assos »
LIGNE_BASE = 225  # ligne de base du texte de ce paragraphe
LIGNE_TAILLE = 42


def nombre_de_la_page(page: Path) -> int:
    """Nombre de signataires annoncé par le compteur de `index.html`."""
    m = RE_COUNT.search(page.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"compteur `signatories__count` introuvable dans {page}")
    return int(m.group(1))


def cadre(dessin: ImageDraw.ImageDraw, texte: str, police: ImageFont.FreeTypeFont):
    """Boîte englobante réelle des glyphes, ancrée en (0, 0)."""
    return dessin.textbbox((0, 0), texte, font=police)


def genere(
    nombre: int,
    gabarit: Path,
    sortie: Path,
    taille_rond: int = ROND_TAILLE,
    taille_ligne: int = LIGNE_TAILLE,
) -> None:
    texte = str(nombre)
    carte = Image.open(gabarit).convert("RGB")
    dessin = ImageDraw.Draw(carte)

    # Rond : le nombre est centré sur le disque, d'après l'encre et non d'après
    # les métriques de la police, dont les jambages inutilisés décaleraient le
    # centrage optique.
    grande = ImageFont.truetype(str(POLICE), taille_rond)
    gauche, haut, droite, bas = cadre(dessin, texte, grande)
    dessin.text(
        (
            ROND_CENTRE[0] - (gauche + droite) / 2,
            ROND_CENTRE[1] - (haut + bas) / 2,
        ),
        texte,
        font=grande,
        fill=ORANGE,
    )

    # Paragraphe : calé par la droite sur le creux du gabarit et posé sur la
    # ligne de base commune à la phrase.
    petite = ImageFont.truetype(str(POLICE), taille_ligne)
    _, _, droite, _ = cadre(dessin, texte, petite)
    dessin.text(
        (LIGNE_DROITE - droite, LIGNE_BASE),
        texte,
        font=petite,
        fill=BLANC,
        anchor="ls",
    )

    carte.save(sortie, optimize=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--nombre",
        type=int,
        help="nombre de signataires (par défaut : compteur de index.html)",
    )
    ap.add_argument(
        "--gabarit", type=Path, default=GABARIT, help="carte vierge en entrée"
    )
    ap.add_argument("--sortie", type=Path, default=SORTIE, help="carte à écrire")
    ap.add_argument(
        "--taille-rond",
        type=int,
        default=ROND_TAILLE,
        help=f"corps du nombre dans le rond (défaut : {ROND_TAILLE})",
    )
    ap.add_argument(
        "--taille-ligne",
        type=int,
        default=LIGNE_TAILLE,
        help=f"corps du nombre dans le paragraphe (défaut : {LIGNE_TAILLE})",
    )
    args = ap.parse_args()

    nombre = args.nombre if args.nombre is not None else nombre_de_la_page(PAGE)
    genere(nombre, args.gabarit, args.sortie, args.taille_rond, args.taille_ligne)
    print(f"{args.sortie} régénérée avec {nombre} signataires.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
