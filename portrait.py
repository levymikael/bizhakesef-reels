"""Série « Portrait » de @bizhakesef : chaque mercredi, une personnalité israélienne du business, d'après son article
Wikipédia (source vérifiable : l'IA n'ajoute rien de mémoire).

Le programme ci-dessous fixe qui passe quand. Chaque mercredi, le premier portrait dont la date est arrivée et qui n'a pas
encore été envoyé est rédigé par Gemini, puis envoyé au scénario Make (« SAVOIR|<json base64>|url ») : l'aperçu arrive sur
Telegram avec les boutons REEL / Carrousel / Rejeter, comme un article. Rien n'est publié sans validation.

Variables : GEMINI_API_KEY, CLOUDINARY_*, MAKE_HOOK_URL, ADMIN_CHAT_ID ; PORTRAIT (facultatif) force une personne.
"""
import base64, datetime, json, os, urllib.parse

import render_reel as R
from notify import tell_make
import photo_choice as PC
from saviez_vous import wiki_text

# (date du mercredi, titre exact de l'article Wikipédia en anglais)
PROGRAMME = [
    ("2026-11-18", "Rami Levy (businessman)"),
    ("2026-11-25", "Omri Casspi"),
    ("2026-12-02", "Dov Moran"),
    ("2026-12-09", "Ofra Strauss"),
    ("2026-12-16", "Gil Shwed"),
    ("2026-12-23", "Amnon Shashua"),
    ("2026-12-30", "Yossi Vardi"),
    ("2027-01-06", "Shari Arison"),
    ("2027-01-13", "Assaf Rappaport"),
    ("2027-01-20", "Kira Radinsky"),
    ("2027-01-27", "Stef Wertheimer"),
    ("2027-02-03", "Eyal Waldman"),
    ("2027-02-10", "Avishai Abrahami"),
]
RESERVE = ["Shlomo Kramer", "Yitzhak Tshuva", "Ziv Aviram"]  # si un article est trop maigre

PROMPT = """Tu es le rédacteur en chef de @bizhakesef, un média Instagram d'information en français sur l'économie israélienne. Ton : factuel, précis, neutre, sans sensationnalisme, sans flatterie ni critique, sans tutoiement, sans question au lecteur.

Tu prépares un post de la série « Portrait » : le parcours d'une personnalité israélienne du monde des affaires, à partir de son article Wikipédia ci-dessous. Le lecteur doit apprendre en 30 secondes qui est cette personne, ce qu'elle a construit et ce qui rend son parcours remarquable.

Règles des faits :
- Uniquement les faits de l'article ci-dessous. Aucun chiffre, aucune date, aucun nom ajouté de mémoire, même s'ils te semblent exacts.
- Parcours professionnel et économique : origines utiles à comprendre le parcours, création ou direction d'entreprises, chiffres clés (taille, valorisation, rachat, salariés, fortune si l'article la donne), apports à l'économie israélienne. Pas de vie privée au-delà de ce qui éclaire le parcours, pas de politique sauf si elle fait partie du parcours économique.
- Si l'article mentionne une affaire judiciaire ou une controverse majeure liée aux affaires, mentionne-la sobrement et factuellement, sans jugement.
- Tout chiffre est daté (« en 2019 ») si l'article le date. Garde la devise d'origine, montants à la française (670 M$, 1,8 Md$, 45 %). Noms propres translittérés correctement depuis l'hébreu.
- Si l'article ne contient pas assez de matière sur le parcours économique de la personne, mets `pertinent` à false.

Le carrousel, 5 à 7 slides, chaque slide se comprend seule :
1. Couverture (`type` = "cover") : `kicker` = « PORTRAIT ». `titre` : le nom de la personne et ce qui rend son parcours frappant, 55 caractères maximum, avec un chiffre s'il y en a un marquant (affirmation, pas de question). Autres champs vides.
2. Étapes du parcours (`type` = "point"), dans l'ordre chronologique : `chiffre` (une date, un montant ou un nombre, 10 caractères maximum, ou vide), `titre` (40 caractères maximum), `texte` (120 caractères maximum), `tendance` ("hausse", "baisse" ou "neutre"), `kicker` vide.
3. « À RETENIR » (`type` = "point", avant-dernière) : `kicker` = « À RETENIR », `titre` vide, `texte` (150 caractères maximum) : ce que ce parcours dit de l'économie israélienne, à partir de l'article. `chiffre` vide, `tendance` "neutre".
4. Fin (`type` = "cta") : `titre` = « L'actualité économique d'Israël, en français », `texte` = « Suivez @bizhakesef et partagez ce post à ceux qui suivent l'économie israélienne. ».
Pas d'emoji dans les slides. Citations entre « ».

La légende (`caption`), 400 à 900 caractères, sans hashtags :
- Première ligne : « Portrait : » suivi du nom et du fait le plus marquant de son parcours, 125 caractères maximum.
- Deux ou trois paragraphes courts : le parcours, puis ce qu'il représente pour l'économie israélienne. Un ou deux emojis sobres.
- Puis une ligne d'appel : « ✉️ Envoyez ce post à quelqu'un qui … », en nommant le public que ce parcours inspirera ou intéressera (par exemple « qui rêve de créer son entreprise en Israël », « qui fait ses courses chez Rami Levy »).
- Puis « Suivez @bizhakesef pour l'actualité économique d'Israël. », puis « 📰 Source : Wikipédia ».

Autres champs : `hashtags` 3 à 5, du plus spécifique au plus large, toujours #EconomieIsraelienne et #Portrait ; `verification` : une entrée par chiffre ou date, « <chiffre> ← <extrait exact de l'article, en anglais> » ; `format_recommande` "reel" ; `interet_grand_public` : note de 1 à 5 suivie d'un tiret et d'une raison de 10 mots maximum (5 : personne connue de tout Israël ou d'une marque du quotidien, 3 : connue du monde des affaires, 1 : peu connue) ; `source_nom` « Wikipédia » ; `categorie` « PORTRAIT » ; `raison_si_non_pertinent` vide si pertinent.

ARTICLE WIKIPÉDIA (« {titre} ») :
"""

DONE = "portraits-faits"  # portraits déjà envoyés, enregistrés chez Cloudinary


def rediger(sujet, schema):
    titre, texte = wiki_text(sujet)
    if len(texte) < 1500:
        print("Article trop court :", titre, len(texte))
        return titre, None
    body = {"contents": [{"parts": [{"text": PROMPT.replace("{titre}", titre) + texte}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema}}
    d = R.first_ok(R.TEXT_MODELS, body)
    out = json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought")))
    if not out.get("pertinent"):
        print("Portrait écarté :", titre, out.get("raison_si_non_pertinent"))
        return titre, None
    return titre, out


def main():
    schema = json.loads(open("schema.json", encoding="utf-8").read())
    faits = (PC.load_plan(DONE) or {}).get("faits", [])
    force = os.environ.get("PORTRAIT", "").strip()
    today = datetime.date.today().isoformat()
    if force:
        file = [force]
    else:
        dus = [t for d, t in PROGRAMME if d <= today and t not in faits]
        if not dus:
            print("Aucun portrait programmé aujourd'hui.")
            return
        file = dus[:1] + [t for t in RESERVE if t not in faits]
    for sujet in file:
        titre, out = rediger(sujet, schema)
        if not force:
            faits.append(sujet)
        if out:
            PC.save_plan(DONE, {"faits": faits})
            url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(titre.replace(" ", "_"))
            print("Portrait :", titre, "|", out["slides"][0]["titre"])
            tell_make("SAVOIR|" + base64.b64encode(json.dumps(out, ensure_ascii=False).encode()).decode() + "|" + url)
            return
    PC.save_plan(DONE, {"faits": faits})
    raise SystemExit("Aucun portrait exploitable")


if __name__ == "__main__":
    main()
