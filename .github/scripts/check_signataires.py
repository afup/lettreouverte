#!/usr/bin/env python3
"""Vérifie qu'une PR ajoutant un ou plusieurs signataires n'a rien oublié.

Compare la liste des signataires entre la branche de base et la tête de la PR,
puis produit une checklist Markdown destinée à un commentaire de PR.

Les vérifications globales (compteur, ordre, carte de partage) portent sur la
page entière ; la vérification du logo est faite signataire par signataire, afin
qu'un ajout groupé ne masque pas celui qui manque.

Le script est volontairement sans dépendance et sort toujours en code 0 : les
échecs de vérification sont un résultat, pas une erreur d'exécution.

Utilisable en local :
    git show main:index.html > /tmp/base.html
    git diff --name-only main > /tmp/fichiers.txt
    python3 .github/scripts/check_signataires.py \
        --base /tmp/base.html --head index.html --changed-files /tmp/fichiers.txt
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime

MARKER = "<!-- lettre-ouverte:check-signataires -->"
SHARE_CARD = "assets/share.png"
# Désignations présentes dans le JSON-LD sans correspondre littéralement à un
# nom affiché dans la grille : raison sociale complète en regard d'un sigle.
ALIAS_TOLERES = {"Association Française des Utilisateurs de PHP"}

RE_SIG = re.compile(r'<li class="sig">(.*?)</li>', re.S)
RE_NAME = re.compile(r'<span class="sig__name">(.*?)</span>', re.S)
RE_IMG_SRC = re.compile(r'<img[^>]*\ssrc="([^"]+)"')
RE_COUNT = re.compile(r'<span class="signatories__count">\s*([0-9]+)\s*</span>')
RE_JSONLD = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)


@dataclass(frozen=True)
class Signataire:
    nom: str
    logo: str | None  # chemin du fichier, ou None si la carte affiche un monogramme


def signataires(source: str) -> list[Signataire]:
    """Signataires de la page, dans l'ordre du document."""
    trouves: list[Signataire] = []
    for bloc in RE_SIG.finditer(source):
        contenu = bloc.group(1)
        nom = RE_NAME.search(contenu)
        if not nom:
            continue
        img = RE_IMG_SRC.search(contenu)
        trouves.append(
            Signataire(
                nom=html.unescape(nom.group(1)).strip(),
                logo=html.unescape(img.group(1)).strip() if img else None,
            )
        )
    return trouves


def compteur(source: str) -> int | None:
    m = RE_COUNT.search(source)
    return int(m.group(1)) if m else None


def cle_tri(nom: str) -> str:
    """Approximation du `localeCompare(nom, 'fr')` utilisé à la génération.

    Insensible à la casse et aux diacritiques, ce qui suffit pour départager
    des noms d'organisations. Reproduit à l'identique l'ordre actuel du fichier.
    """
    decompose = unicodedata.normalize("NFD", nom)
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return sans_accent.casefold()


def desordres(noms: list[str]) -> list[tuple[str, str]]:
    """Toutes les paires consécutives mal ordonnées."""
    return [
        (precedent, suivant)
        for precedent, suivant in zip(noms, noms[1:])
        if cle_tri(precedent) > cle_tri(suivant)
    ]


def statut_logo(
    sig: Signataire, fichiers: list[str], racine: str
) -> tuple[bool, str]:
    """Le nouveau signataire a-t-il un logo exploitable ?"""
    if sig.logo is None:
        return False, "monogramme par défaut, aucun logo fourni"
    if sig.logo in fichiers:
        return True, f"`{sig.logo}` ajouté dans la PR"
    if os.path.exists(os.path.join(racine, sig.logo)):
        return True, f"`{sig.logo}` déjà présent dans le dépôt"
    return False, f"`{sig.logo}` référencé mais introuvable"


def _charger_jsonld(source: str) -> tuple[dict[str, dict], list[dict], str]:
    """(nœuds indexés par @id, nœuds Article, raison d'échec)."""
    blocs = RE_JSONLD.findall(source)
    if not blocs:
        return {}, [], "aucun bloc `application/ld+json` dans la page"

    noeuds: dict[str, dict] = {}
    articles: list[dict] = []
    for bloc in blocs:
        try:
            doc = json.loads(bloc)
        except json.JSONDecodeError as err:
            return {}, [], f"JSON-LD illisible ({err.msg}, ligne {err.lineno})"
        graphe = doc.get("@graph", doc) if isinstance(doc, dict) else doc
        if isinstance(graphe, dict):
            graphe = [graphe]
        for noeud in graphe:
            if not isinstance(noeud, dict):
                continue
            if "@id" in noeud:
                noeuds[noeud["@id"]] = noeud
            if noeud.get("@type") == "Article":
                articles.append(noeud)
    return noeuds, articles, ""


def date_modifiee(source: str) -> tuple[str | None, str]:
    """Valeur brute de `dateModified` sur le nœud Article."""
    _, articles, erreur = _charger_jsonld(source)
    if erreur:
        return None, erreur
    if not articles:
        return None, "aucun nœud `Article` dans le JSON-LD"
    for article in articles:
        valeur = article.get("dateModified")
        if isinstance(valeur, str) and valeur.strip():
            return valeur.strip(), ""
    return None, "`dateModified` absente du nœud `Article`"


def _en_date(valeur: str) -> datetime | None:
    """Parse un ISO 8601, en tolérant le suffixe Z (Python < 3.11)."""
    try:
        return datetime.fromisoformat(valeur.replace("Z", "+00:00"))
    except ValueError:
        return None


def noms_jsonld(source: str) -> tuple[set[str] | None, str]:
    """Noms des organisations déclarées `author` dans le JSON-LD.

    Retourne (None, raison) si le bloc est absent ou inexploitable : on préfère
    une case décochée explicite à un faux positif silencieux.
    """
    noeuds, articles, erreur = _charger_jsonld(source)
    if erreur:
        return None, erreur

    auteurs: list[dict] = []
    for article in articles:
        brut = article.get("author", [])
        auteurs += brut if isinstance(brut, list) else [brut]

    if not auteurs:
        return None, "aucun nœud `Article` avec une propriété `author`"

    noms: set[str] = set()
    for auteur in auteurs:
        cible = noeuds.get(auteur["@id"], {}) if "@id" in auteur else auteur
        # `name` et `alternateName` sont deux désignations valides de la même
        # organisation : l'AFUP est nommée en toutes lettres dans le graphe et
        # par son sigle dans la grille.
        for champ in ("name", "alternateName"):
            valeur = cible.get(champ)
            if isinstance(valeur, str) and valeur.strip():
                noms.add(valeur.strip())
    return noms, ""


def coche(ok: bool) -> str:
    return "x" if ok else " "


def construire_rapport(
    base: str, head: str, fichiers: list[str], racine: str
) -> tuple[bool, str]:
    """Retourne (nouveau_signataire_detecte, corps_du_commentaire)."""
    sig_base = signataires(base)
    sig_head = signataires(head)

    noms_base = {s.nom for s in sig_base}
    noms_head = {s.nom for s in sig_head}
    ajoutes = [s for s in sig_head if s.nom not in noms_base]
    if not ajoutes:
        return False, ""

    titre = (
        "## Nouveau signataire détecté"
        if len(ajoutes) == 1
        else f"## {len(ajoutes)} nouveaux signataires détectés"
    )
    lignes: list[str] = [MARKER, titre, ""]
    for s in ajoutes:
        lignes.append(f"- **{s.nom}**")
    retires = [s.nom for s in sig_base if s.nom not in noms_head]
    if retires:
        lignes.append("")
        lignes.append(
            "Signataire(s) retiré(s) au passage : "
            + ", ".join(f"**{n}**" for n in retires)
        )
    lignes += ["", "### Checklist", ""]

    # 1. Compteur affiché cohérent avec le nombre réel de cartes.
    declare = compteur(head)
    reel = len(sig_head)
    if declare is None:
        compteur_ok = False
        detail = "compteur introuvable dans `index.html`"
    else:
        compteur_ok = declare == reel
        detail = (
            f"`{declare}` annoncé pour {reel} signataire(s)"
            if compteur_ok
            else f"`{declare}` annoncé mais {reel} signataire(s) dans la page"
        )
    lignes.append(f"- [{coche(compteur_ok)}] **Compteur mis à jour** — {detail}")

    # 2. Ordre alphabétique de la grille.
    fautes = desordres([s.nom for s in sig_head])
    ordre_ok = not fautes
    if ordre_ok:
        detail = "la liste est bien triée"
    else:
        paires = " ; ".join(f"« {apres} » avant « {avant} »" for avant, apres in fautes)
        pluriel = "problème" if len(fautes) == 1 else "problèmes"
        detail = f"{len(fautes)} {pluriel} — attendu : {paires}"
    lignes.append(f"- [{coche(ordre_ok)}] **Ordre alphabétique respecté** — {detail}")

    # 3. Carte de partage régénérée (présence seule, pas le contenu).
    carte_ok = SHARE_CARD in fichiers
    detail = (
        f"`{SHARE_CARD}` fait partie de la PR"
        if carte_ok
        else f"`{SHARE_CARD}` est absent de la PR alors qu'il affiche le nombre de signataires"
    )
    lignes.append(f"- [{coche(carte_ok)}] **Carte de partage régénérée** — {detail}")

    # 4. Logo : vérifié pour chaque nouveau signataire séparément.
    resultats = [(s, *statut_logo(s, fichiers, racine)) for s in ajoutes]
    logos_ok = all(ok for _, ok, _ in resultats)
    nb_ok = sum(1 for _, ok, _ in resultats if ok)
    if len(resultats) == 1:
        # Un seul ajout : le détail tient sur la ligne, pas de sous-liste.
        entete = resultats[0][2]
        titre_logo = "**Logo fourni**"
    else:
        entete = "tous fournis" if logos_ok else f"{nb_ok}/{len(resultats)} fournis"
        titre_logo = "**Logo fourni pour chaque nouveau signataire**"
    lignes.append(f"- [{coche(logos_ok)}] {titre_logo} — {entete}")
    if len(resultats) > 1:
        for sig, ok, detail in resultats:
            lignes.append(f"  - [{coche(ok)}] {sig.nom} — {detail}")

    # 5. Données structurées : la liste des signataires y est dupliquée.
    declares, raison = noms_jsonld(head)
    if declares is None:
        jsonld_ok = False
        detail = raison
    else:
        attendus = {s.nom for s in sig_head}
        absents = sorted(n for n in attendus if n not in declares)
        en_trop = sorted(
            n for n in declares if n not in attendus and n not in ALIAS_TOLERES
        )
        jsonld_ok = not absents and not en_trop
        if jsonld_ok:
            detail = f"{len(attendus)} organisation(s), cohérent avec la grille"
        else:
            morceaux = []
            if absents:
                morceaux.append("absent(s) du JSON-LD : " + ", ".join(absents))
            if en_trop:
                morceaux.append("présent(s) en trop : " + ", ".join(en_trop))
            detail = " ; ".join(morceaux)
    lignes.append(f"- [{coche(jsonld_ok)}] **JSON-LD synchronisé** — {detail}")

    # 6. dateModified : ajouter un signataire modifie la page, la date doit suivre.
    brut_base, _ = date_modifiee(base)
    brut_head, raison_date = date_modifiee(head)
    if brut_head is None:
        date_ok, detail = False, raison_date
    elif (d_head := _en_date(brut_head)) is None:
        date_ok = False
        detail = f"`{brut_head}` n'est pas une date ISO 8601 valide"
    elif brut_base is None:
        date_ok, detail = True, f"`{brut_head}`"
    elif brut_head == brut_base:
        date_ok = False
        detail = f"inchangée depuis la branche de base (`{brut_base}`)"
    elif (d_base := _en_date(brut_base)) is not None and d_head < d_base:
        date_ok = False
        detail = f"`{brut_head}` est antérieure à la base (`{brut_base}`)"
    else:
        date_ok, detail = True, f"`{brut_base}` → `{brut_head}`"
    lignes.append(f"- [{coche(date_ok)}] **`dateModified` mise à jour** — {detail}")

    lignes += ["", "---", ""]
    if compteur_ok and ordre_ok and carte_ok and logos_ok and jsonld_ok and date_ok:
        lignes.append("Tout est en ordre.")
    else:
        lignes.append(
            "Les points non cochés sont à corriger avant fusion. "
            "Cette vérification est indicative et ne bloque pas la PR."
        )
    lignes += [
        "",
        "<sub>Vérification automatique — relancée à chaque push sur la PR.</sub>",
    ]

    return True, "\n".join(lignes)


def lire(chemin: str) -> str:
    if chemin == "-":
        return sys.stdin.read()
    with open(chemin, encoding="utf-8") as f:
        return f.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True, help="index.html de la branche de base")
    ap.add_argument("--head", required=True, help="index.html de la tête de PR")
    ap.add_argument(
        "--changed-files",
        required=True,
        help="fichier listant les chemins modifiés par la PR, un par ligne ('-' pour stdin)",
    )
    ap.add_argument(
        "--repo-root",
        default=".",
        help="racine du dépôt en version de base, pour tester la présence d'un logo existant",
    )
    ap.add_argument("--out", help="écrire le commentaire dans ce fichier")
    args = ap.parse_args()

    base = lire(args.base)
    head = lire(args.head)
    fichiers = [ligne.strip() for ligne in lire(args.changed_files).splitlines() if ligne.strip()]

    nouveau, corps = construire_rapport(base, head, fichiers, args.repo_root)

    if args.out and corps:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(corps)

    sortie = os.environ.get("GITHUB_OUTPUT")
    if sortie:
        with open(sortie, "a", encoding="utf-8") as f:
            f.write(f"nouveau_signataire={'true' if nouveau else 'false'}\n")

    if corps:
        print(corps)
    else:
        print("Aucun nouveau signataire dans cette PR — rien à vérifier.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
