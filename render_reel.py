"""Monte un reel « motion design » 1080x1920 à partir des slides d'un carrousel Bizhakesef.

Étapes :
1. Gemini lit les slides et écrit, pour chaque scène, la phrase de voix off et le contenu à animer
   (sur-titre, titre, chiffre, tendance), et nomme la société ou la personne principale.
2. Photo de cette société ou personne récupérée sur Wikipédia (Wikimedia Commons), affichée en couverture.
3. Synthèse vocale Gemini, détection des pauses : chaque scène démarre au début de SA phrase.
4. Animation HTML (reel.html) filmée image par image dans Chromium (Playwright), puis FFmpeg :
   voix, fond rythmé qui s'efface sous la voix, whoosh à chaque transition.

Usage :
  python render_reel.py job.json sortie.mp4
job.json : {"id": "...", "slides": ["https://.../1.jpg", ...], "caption": "...", "titre": "..."}
Variables : GEMINI_API_KEY (obligatoire), FFMPEG (défaut « ffmpeg »), PW_CHANNEL (ex. « msedge » en local).
Musique : un fichier music.mp3 posé à côté de ce script remplace le fond rythmé synthétisé.
"""
import hashlib, base64, json, os, re, subprocess, sys, tempfile, unicodedata, urllib.parse, urllib.request, wave
from pathlib import Path

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
KEY = os.environ["GEMINI_API_KEY"]
TEXT_MODELS = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-flash-latest",
               "gemini-3.1-flash-lite"]  # Lite d'abord (Flash limité à 20 requêtes/jour) ; relais sur d'autres générations
TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
VOICE = os.environ.get("REEL_VOICE", "Charon")
W, H, FPS = 1080, 1920, 30
SPEED = 1.1        # voix légèrement accélérée
LEAD = 0.15        # la voix démarre presque immédiatement (accroche)
TAIL = 0.9
MUSIC_VOL = 0.5    # fond musical, atténué automatiquement quand la voix parle
UA = {"User-Agent": "BizhakesefReel/1.0 (https://github.com/levymikael/bizhakesef-reels)"}

PROMPT = """Tu prépares un reel Instagram de @bizhakesef, un média d'information en français sur l'économie israélienne.
Les {N} images jointes sont les slides du carrousel, dans l'ordre. Le reel aura exactement {N} scènes : la scène n°k reprend la slide n°k.

Pour chaque scène :
- `phrase` : la phrase de voix off lue pendant la scène, 6 à 12 mots (limite stricte : le reel entier doit durer moins de 25 secondes), courte et rythmée. Ton de présentateur de journal économique : factuel, neutre, pas de tutoiement, pas de question. Nombres en toutes lettres comme ils se prononcent (« quatre virgule quatre milliards de dollars »). La phrase 1 est une accroche forte avec le fait ou le chiffre le plus marquant.
- `kicker` : sur-titre de 1 à 3 mots en majuscules (ex. « RÉSULTATS », « CHIFFRE D'AFFAIRES »).
- `titre` : le message de la scène, 50 caractères maximum, nombres écrits en chiffres (« 237,8 Md$ », jamais en lettres), le shekel écrit ₪ (« 6 Md₪ », jamais NIS). Ne recopie pas le texte de la slide : résume-le. Jamais un libellé générique comme « À retenir » ou « Baisse » : le titre énonce l'information elle-même.
- `texte` : précision utile en 85 caractères maximum, qui apporte un fait de plus (ne répète ni le titre ni le chiffre), ou "". Ici, les nombres s'écrivent en chiffres (« 185 M$ », « 1,82 $ »).
- `chiffre` : le chiffre clé tel qu'affiché, court, avec unité et signe (ex. « 670 M$ », « -45 % », « +3,2 % »), ou "".
- `tendance` : « hausse », « baisse » ou « neutre ».
- `entite_wikipedia` : si la scène parle surtout d'une autre société ou personne que l'entité principale, son titre exact sur Wikipédia en anglais ; sinon "".
La scène 1 est l'accroche, décisive : la plupart des spectateurs décident de rester ou de passer en moins de 3 secondes. Pour elle seule :
- `chiffre` obligatoire dès que l'information comporte un nombre (montant, pourcentage, prix, record, date) : le plus frappant.
- `titre` : le fait et sa conséquence concrète, jamais un simple sujet. « Taxe sur les billets d'avion » est interdit ; « Une nouvelle taxe sur chaque billet d'avion » convient.
- `kicker` : le sujet précis en 1 à 3 mots (« BILLETS D'AVION », « EL AL », « SHEKEL »), jamais une rubrique (« MACRO », « TECH », « ENTREPRISES », « MARCHÉS »).
- `phrase` : 5 à 9 mots, qui commence directement par le fait ou le chiffre, sans préambule (« Israël envisage », « Selon », « Voici »).
La dernière scène est la scène de fin : `phrase` exactement « Suivez Bizhakesef pour l'actualité économique d'Israël. », les autres champs vides.

`entite` : la société, la personnalité ou l'institution au cœur de l'information. `nom` : son nom usuel. `wikipedia` : son titre exact sur Wikipédia en anglais (ex. « Teva Pharmaceuticals », « Bezalel Smotrich », « Bank of Israel »). `type` : « entreprise », « personne », « institution », ou « aucune » pour un sujet général. `site` : pour une société ou une institution, le domaine de son site officiel (« biocatch.com », « boi.org.il »), seulement si tu en es certain ; sinon "".
`acteurs` : les AUTRES sociétés ou personnes au cœur de l'information, en plus de `entite` (l'acheteur et la cible d'un rachat, les deux partenaires d'un accord, deux concurrents comparés, le dirigeant qui part et celui qui arrive), au plus 2, avec `nom`, `wikipedia`, `type`, `recherche_photo` et `site` comme pour `entite` ; [] si l'information ne concerne vraiment qu'un acteur. Jamais une institution qui se contente d'autoriser ou de commenter.
`recherche_photo` : deux ou trois requêtes en anglais séparées par « | », de la plus précise à la plus large, pour trouver la meilleure photo de couverture dans une banque d'images : le sujet lui-même s'il se photographie, sinon une image concrète qui l'illustre. Pour une société ou une institution, commence par son logo ; pour une personne, son nom. Jamais de bureaux ni de siège. Pour un sujet général (type « aucune »), mêmes règles que `theme_photo` ci-dessous : une scène concrète qui montre le fait, jamais une requête vague (« Israel tech ecosystem », « Israel economy »). Exemples : « El Al logo | El Al Boeing 787 », « Bank of Israel logo | Bank of Israel emblem », « Benjamin Netanyahu », « new israeli shekel | shekel coins », « Tel Aviv construction cranes | apartment construction Israel ».
`theme_photo` : trois requêtes en anglais séparées par « | » pour une image concrète et spectaculaire de l'OBJET de l'information (le secteur, le produit, l'activité touchée), indépendamment de l'entité, ancrée en Israël quand c'est possible (compagnie, lieu ou objet israélien), et précise (« passenger airliner », pas « airplane », qui ramène des avions militaires). Exemples : taxe sur les vols → « El Al Boeing 787 | Ben Gurion airport | passenger airliner takeoff » ; gaz → « offshore gas platform | gas rig Mediterranean » ; immobilier → « construction cranes Tel Aviv | apartment towers construction » ; shekel → « shekel banknotes | shekel coins » ; puces → « semiconductor wafer | computer chip ». Quand le sujet est abstrait ou social (licenciements, salaires, chômage, inflation, impôts, budget, coût de la vie), décris une SCÈNE HUMAINE concrète qui montre le fait, comme dans une banque de photos d'agence : licenciements tech → « employee carrying cardboard box leaving office | empty office desks | worker packing belongings desk » ; salaires → « payslip salary | worker counting money » ; impôts → « tax form calculator | paying taxes » ; coût de la vie → « supermarket shopping cart prices | grocery receipt ». Jamais de requête vague comme « Israel economy » ou « tel aviv startup ». La troisième requête est toujours un ou deux mots-clés simples, comme on les tape dans une banque d'images (« layoffs », « unemployment », « salary », « inflation »).

Uniquement les faits des slides et de la légende. Chaque nombre, dit ou affiché, doit être exactement celui de la slide (si la slide affiche 670 M$, la voix dit « six cent soixante-dix millions de dollars »). Pas d'emoji, pas de hashtag.

LÉGENDE :
"""
SCENE = {"type": "object", "properties": {k: {"type": "string"} for k in
         ("phrase", "kicker", "titre", "texte", "chiffre", "tendance", "entite_wikipedia")},
         "required": ["phrase", "kicker", "titre", "texte", "chiffre", "tendance", "entite_wikipedia"]}
SCHEMA = {"type": "object", "required": ["entite", "acteurs", "scenes"], "properties": {
    "entite": {"type": "object", "required": ["nom", "wikipedia", "type", "recherche_photo", "theme_photo"],
               "properties": {"nom": {"type": "string"}, "wikipedia": {"type": "string"}, "type": {"type": "string"},
                              "recherche_photo": {"type": "string"}, "theme_photo": {"type": "string"},
                              "site": {"type": "string"}}},
    "acteurs": {"type": "array", "items": {"type": "object", "required": ["nom", "wikipedia", "type", "recherche_photo"],
                "properties": {k: {"type": "string"} for k in ("nom", "wikipedia", "type", "recherche_photo", "site")}}},
    "scenes": {"type": "array", "items": SCENE}}}


def gemini(model, body):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(), headers={"x-goog-api-key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def mistral(body):
    """Dernier recours hors Google : la requête Gemini est traduite pour Mistral, et la réponse remise au format Gemini."""
    content = []
    for p in body["contents"][0]["parts"]:
        if "text" in p:
            content.append({"type": "text", "text": p["text"]})
        elif "inlineData" in p:
            content.append({"type": "image_url", "image_url": f"data:{p['inlineData']['mimeType']};base64,{p['inlineData']['data']}"})
    gc = body.get("generationConfig", {})
    req = {"model": "mistral-medium-latest", "temperature": 0.3, "messages": [{"role": "user", "content": content}]}
    if gc.get("responseJsonSchema"):
        req["response_format"] = {"type": "json_object"}
        content.append({"type": "text", "text": "Réponds uniquement par un objet JSON conforme à ce schéma JSON :\n"
                        + json.dumps(gc["responseJsonSchema"], ensure_ascii=False)})
    r = urllib.request.Request("https://api.mistral.ai/v1/chat/completions", data=json.dumps(req).encode(),
                               headers={"Authorization": "Bearer " + os.environ["MISTRAL_API_KEY"].strip(),
                                        "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=180) as resp:
        txt = json.load(resp)["choices"][0]["message"]["content"]
    return {"candidates": [{"content": {"parts": [{"text": txt}]}}]}


def first_ok(models, body):
    import time
    last = None
    for attempt in range(3):
        for m in models:
            try:
                return gemini(m, body)
            except Exception as e:  # 503 / 429 fréquents sur l'offre gratuite : on alterne et on patiente
                last = e
        # Texte (pas la voix) : Mistral prend le relais si Gemini est saturé
        is_text = "AUDIO" not in body.get("generationConfig", {}).get("responseModalities", [])
        if is_text and os.environ.get("MISTRAL_API_KEY"):
            try:
                return mistral(body)
            except Exception as e:
                last = e
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Gemini indisponible : {last}")


def analyse(caption, slides, maj=""):
    prompt = PROMPT.replace("{N}", str(len(slides)))
    if maj:  # actualisation de dernière minute (cours en direct) : seule exception à la règle des chiffres des slides
        prompt = prompt.replace("LÉGENDE :", "MISE À JOUR PRIORITAIRE, qui l'emporte sur les slides : " + maj + "\n\nLÉGENDE :")
    parts = [{"text": prompt + caption}]
    for p in slides:
        data = Path(p).read_bytes()
        parts.append({"inlineData": {"mimeType": "image/png" if data[:4] == b"\x89PNG" else "image/jpeg",
                                     "data": base64.b64encode(data).decode()}})
    d = first_ok(TEXT_MODELS, {"contents": [{"parts": parts}],
                               "generationConfig": {"responseMimeType": "application/json",
                                                    "responseJsonSchema": SCHEMA}})
    txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought"))
    out = json.loads(txt)
    sc, n = [x for x in out["scenes"] if x.get("phrase", "").strip()], len(slides)
    if len(sc) > n:  # excédent regroupé sur l'avant-dernière scène
        sc[n - 2]["phrase"] = " ".join(x["phrase"] for x in sc[n - 2:-1])
        sc = sc[:n - 1] + [sc[-1]]
    while len(sc) < n:
        sc.insert(-1, dict(sc[-2]))
    out["scenes"] = sc
    if len(sc) > 2 and not sc[0].get("chiffre"):  # accroche : le chiffre le plus fort s'affiche dès la 1re seconde
        src = next((x for x in sc[1:-1] if x.get("chiffre")), None)
        if src:
            sc[0].update(chiffre=src["chiffre"], tendance=src.get("tendance", "neutre"))
    norm = lambda c: re.sub(r"\s+", "", (c or "")).lower()
    if len(sc) > 4 and sc[0].get("chiffre"):  # une scène qui répète le chiffre de l'accroche est retirée (reel plus court)
        out["retire"] = [k for k in range(1, len(sc) - 1) if norm(sc[k].get("chiffre")) == norm(sc[0]["chiffre"])][:1]
        sc = out["scenes"] = [s for k, s in enumerate(sc) if k not in out["retire"]]
    shorten(sc)
    return out


LIMITS = {"titre": 50, "texte": 85, "kicker": 22}


def shorten(scenes):
    """À l'écran, il faut des textes courts : tout champ trop long est réécrit plus court par Gemini."""
    todo = [{"i": i, "champ": k, "max": m, "texte": s[k]} for i, s in enumerate(scenes)
            for k, m in LIMITS.items() if len(s.get(k, "")) > m]
    if not todo:
        return _clean(scenes)
    body ={"contents": [{"parts": [{"text": (
        "Raccourcis chaque texte sous sa longueur maximale en caractères (champ `max`), en français, "
        "sans changer ni inventer aucun fait ni aucun chiffre. Style titre de journal, nombres en chiffres. "
        "Réponds avec la même liste, où `texte` est raccourci.\n" + json.dumps(todo, ensure_ascii=False))}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": {
            "type": "array", "items": {"type": "object", "required": ["i", "champ", "texte"], "properties": {
                "i": {"type": "integer"}, "champ": {"type": "string"}, "texte": {"type": "string"}}}}}}
    try:
        d = first_ok(TEXT_MODELS, body)
        txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought"))
        for r in json.loads(txt):
            if 0 <= r["i"] < len(scenes) and r["champ"] in LIMITS:
                scenes[r["i"]][r["champ"]] = r["texte"]
    except Exception as e:
        print("raccourci", e, file=sys.stderr)
    _clean(scenes)


def _clean(scenes):
    for s in scenes:  # garde-fou : un texte secondaire encore trop long, ou qui répète le chiffre, est retiré
        for k in ("chiffre", "titre", "texte", "kicker"):  # le shekel s'écrit ₪ à l'écran, jamais NIS
            if s.get(k):
                s[k] = re.sub(r"(\d|Md|M|k)\s*(?:NIS|ILS)\b", r"\1₪", s[k]).replace(" NIS", " shekels")
        t = s.get("texte", "")
        if len(t) > LIMITS["texte"] + 25 or len(_norm(t)) <= 3 or _norm(t) == _norm(s.get("chiffre", "")):
            s["texte"] = ""


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", s)


VERIF_SCHEMA = {"type": "object", "required": ["correspond", "raison"],
                "properties": {"correspond": {"type": "boolean"}, "raison": {"type": "string"}}}
PHOTO_LOG = []  # décisions de vérification, enregistrées dans plan.json


def verify_photo(nom, contexte, page, desc, data, mime):
    """Contre-vérification par Gemini : la page Wikipédia et son image représentent-elles bien l'entité de l'article ?"""
    prompt = (f"Un reel d'actualité économique israélienne doit montrer en couverture une photo de « {nom} ».\n"
              f"Extrait de l'article : {contexte[:900]}\n\n"
              f"Page Wikipédia trouvée : « {page} » — description : « {desc or 'aucune'} ». L'image jointe est l'image de cette page.\n"
              "La page ET l'image représentent-elles exactement la même entité que celle dont parle l'article "
              "(même société, même personne, même institution), et pas un homonyme, un produit, un lieu ou un autre sujet ? "
              "Exemple : pour la start-up Sentra, une page sur la voiture Nissan Sentra ne correspond PAS. "
              "Au moindre doute, réponds correspond = false.")
    body = {"contents": [{"parts": [{"text": prompt},
                                    {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": VERIF_SCHEMA}}
    try:
        d = first_ok(TEXT_MODELS, body)
        out = json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought")))
    except Exception as e:  # sans vérification possible, pas de photo
        out = {"correspond": False, "raison": f"vérification impossible : {e}"}
    PHOTO_LOG.append({"nom": nom, "page": page, "description": desc, **out})
    return bool(out.get("correspond"))


def wiki_photo(title, nom="", contexte=""):
    """Image principale de la page Wikipédia (photo d'une personne, logo ou siège d'une société),
    retenue seulement si Gemini confirme qu'elle correspond bien à l'entité de l'article."""
    if not title and not nom:
        return None
    nom = nom or title
    checked = set()
    for lang in ("en", "fr", "he"):
        try:
            def q(**params):
                u = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                    {**params, "format": "json", "formatversion": 2})
                with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
                    return json.load(r)
            img = dict(action="query", prop="pageimages|description", piprop="thumbnail", pithumbsize=1200, redirects=1)
            # Candidats : le titre exact proposé, puis les premiers résultats de recherche
            cands = []
            pg = q(titles=title or nom, **img)["query"]["pages"][0]
            if not pg.get("missing"):
                cands.append(pg)
            for h in q(action="query", list="search", srsearch=nom, srlimit=3)["query"]["search"]:
                if h["title"] not in [c.get("title") for c in cands]:
                    cands.append(q(titles=h["title"], **img)["query"]["pages"][0])
            for found in cands:
                key = (lang, found.get("title"))
                if not found.get("thumbnail") or key in checked or len(checked) >= 4:
                    continue
                checked.add(key)
                url = found["thumbnail"]["source"]
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                    data = r.read()
                fname = urllib.parse.unquote(url.split("/")[-1]).lower()
                mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
                if not verify_photo(nom, contexte, found.get("title"), found.get("description", ""), data, mime):
                    continue
                return {"src": f"data:{mime};base64," + base64.b64encode(data).decode(),
                        "logo": ".svg" in fname or "logo" in fname or mime == "image/png",
                        "page": found.get("title")}
        except Exception as e:
            print("photo", lang, title, e, file=sys.stderr)
    return None


# --- Photo de couverture : plusieurs sources libres, puis Gemini choisit la meilleure en une fois ---
PICK_SCHEMA = {"type": "object", "required": ["choix", "raison"],
               "properties": {"choix": {"type": "integer"}, "raison": {"type": "string"}}}


def _get_json(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or UA), timeout=30) as r:
        return json.load(r)


def _wiki_cands(title, nom):
    out = []
    for lang in ("en", "fr"):
        base = f"https://{lang}.wikipedia.org/w/api.php?"
        img = dict(action="query", prop="pageimages|description", piprop="thumbnail", pithumbsize=1000, redirects=1,
                   format="json", formatversion=2)
        titles = [title or nom] if lang == "en" else []
        titles += [h["title"] for h in _get_json(base + urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": nom or title, "srlimit": 2, "format": "json",
             "formatversion": 2}))["query"]["search"]]
        for t in dict.fromkeys(x for x in titles if x):
            pg = _get_json(base + urllib.parse.urlencode({**img, "titles": t}))["query"]["pages"][0]
            if pg.get("thumbnail"):
                out.append({"url": pg["thumbnail"]["source"], "credit": "Photo : Wikimedia Commons",
                            "label": f"image de la page Wikipédia « {pg.get('title')} » ({pg.get('description', '')})"})
    return out


def _commons_cands(q, n=6):
    d = _get_json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": f"{q} filetype:bitmap", "gsrlimit": n,
        "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": 1000, "format": "json", "formatversion": 2}))
    out = []
    for pg in d.get("query", {}).get("pages", []):
        i = (pg.get("imageinfo") or [{}])[0]
        if i.get("mime") == "image/jpeg" and i.get("width", 0) >= 700 and i.get("thumburl"):
            out.append({"url": i["thumburl"], "credit": "Photo : Wikimedia Commons", "label": pg["title"][5:-4]})
    return out


def _openverse_cands(q, n=6, page=1):
    d = _get_json("https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(
        {"q": q, "page_size": 12, "page": page, "license_type": "commercial", "mature": "false"}))
    out, seen = [], set()
    for r in d.get("results", []):
        key = (r.get("creator"), (r.get("title") or "")[:25])
        if (r.get("width") or 0) < 700 or key in seen:
            continue
        seen.add(key)
        src = {"flickr": "Flickr", "wikimedia": "Wikimedia Commons"}.get(r.get("source") or "", r.get("source") or "")
        out.append({"url": r["url"], "label": r.get("title") or "",
                    "credit": f"Photo : {r.get('creator') or src} / {src}, CC {r.get('license', '').upper()}"})
        if len(out) >= n:
            break
    return out


def _pexels_cands(q, n=5):
    k = os.environ.get("PEXELS_API_KEY")
    if not k:
        return []
    d = _get_json("https://api.pexels.com/v1/search?" + urllib.parse.urlencode({"query": q, "per_page": n}),
                  {**UA, "Authorization": k})
    return [{"url": p["src"]["large"], "label": p.get("alt") or "", "credit": f"Photo : {p.get('photographer')} / Pexels"}
            for p in d.get("photos", [])]


def _pixabay_cands(q, n=4):
    k = os.environ.get("PIXABAY_API_KEY")
    if not k:
        return []
    d = _get_json("https://pixabay.com/api/?" + urllib.parse.urlencode(
        {"key": k, "q": q[:100], "image_type": "photo", "per_page": max(n, 3), "safesearch": "true", "min_width": 900}))
    return [{"url": h["largeImageURL"], "label": h.get("tags") or "", "credit": f"Photo : {h.get('user')} / Pixabay"}
            for h in d.get("hits", [])[:n]]


def _unsplash_cands(q, n=4):
    k = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not k:
        return []
    d = _get_json("https://api.unsplash.com/search/photos?" + urllib.parse.urlencode(
        {"query": q, "per_page": n, "content_filter": "high"}), {**UA, "Authorization": f"Client-ID {k}", "Accept-Version": "v1"})
    return [{"url": r["urls"]["regular"], "label": r.get("alt_description") or r.get("description") or "",
             "credit": f"Photo : {r['user']['name']} / Unsplash"} for r in d.get("results", [])[:n]]


RANK_SCHEMA = {"type": "object", "required": ["classement", "raison"], "properties": {
    "classement": {"type": "array", "items": {"type": "object", "required": ["image", "logo"],
                                              "properties": {"image": {"type": "integer"}, "logo": {"type": "boolean"}}}},
    "raison": {"type": "string"}}}


def _logo_cands(nom, n=5):
    """Logos sur Wikimedia Commons (les SVG sont rendus en PNG par Wikimedia)."""
    d = _get_json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": f"{nom} logo", "gsrlimit": n,
        "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": 800, "format": "json", "formatversion": 2}))
    out = []
    for pg in d.get("query", {}).get("pages", []):
        i = (pg.get("imageinfo") or [{}])[0]
        if i.get("mime") in ("image/svg+xml", "image/png", "image/jpeg") and i.get("thumburl"):
            out.append({"url": i["thumburl"], "credit": "Logo : Wikimedia Commons", "label": pg["title"][5:]})
    return out


def _site_cands(site):
    """Le site officiel d'une société : son image de partage (og:image) et son icône, quand rien n'existe sur Wikimédia."""
    dom = re.sub(r"^https?://|/.*$", "", (site or "").strip().lower())
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", dom):
        return []
    req = urllib.request.Request("https://" + dom, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=20) as r:
        base, page = r.geturl(), r.read(600000).decode("utf-8", "replace")
    out = []
    for tag in re.findall(r"<(?:meta|link)[^>]*>", page, re.I):
        attr = dict((k.lower(), v) for k, v in re.findall(r"""([\w:-]+)\s*=\s*["']([^"']*)["']""", tag))
        kind = (attr.get("property") or attr.get("name") or attr.get("rel") or "").lower()
        url = attr.get("content") if tag[1:5].lower() == "meta" else attr.get("href")
        if not url:
            continue
        if kind in ("og:image", "twitter:image"):
            out.append({"url": urllib.parse.urljoin(base, url), "credit": f"Image : {dom}", "label": f"Site officiel {dom} (image de partage)"})
        elif "apple-touch-icon" in kind or ("icon" in kind and re.search(r"\.png", url, re.I)):
            out.append({"url": urllib.parse.urljoin(base, url), "credit": f"Logo : {dom}", "label": f"Site officiel {dom} (icône)", "logo": True})
    return list({c["url"]: c for c in out}.values())[:4]


def cover_candidates(ent, contexte, k=4, exclure=(), autres=False, force=False):
    """Photos candidates pour la couverture, classées par Gemini : le logo pour une société ou une institution,
    le portrait pour une personne, une image concrète pour un sujet général ; jamais un bâtiment.
    Renvoie au plus k candidats [{url, credit, label, logo, data, mime}], du meilleur au moins bon."""
    from concurrent.futures import ThreadPoolExecutor
    nom, q, typ = ent.get("nom", ""), ent.get("recherche_photo") or ent.get("nom", ""), ent.get("type", "aucune")
    if not (nom or q):
        return []
    qs = [x.strip() for x in q.split("|") if x.strip()][:3]
    theme = [x.strip() for x in (ent.get("theme_photo") or "").split("|") if x.strip()][:3]
    qs += [x for x in theme if x not in qs]  # toujours une image du secteur touché, en plus du logo ou du portrait
    if autres:  # nouvelle recherche : d'autres angles du même sujet
        qs += {"entreprise": [f"{nom} CEO", f"{nom} founder", f"{nom} product"],
               "institution": [f"{nom} governor", f"{nom} emblem"],
               "personne": [f"{nom} speech", f"{nom} conference"]}.get(typ, [f"{t} photo" for t in theme] or [nom])
    jobs = [lambda: _wiki_cands(ent.get("wikipedia"), nom) if typ != "aucune" else []]  # sujet général : pas de page Wikipédia hasardeuse
    if typ in ("entreprise", "institution") and nom:
        jobs.append(lambda: _logo_cands(ent.get("wikipedia") or nom))
    if typ in ("entreprise", "institution") and ent.get("site"):
        jobs.append(lambda: _site_cands(ent["site"]))
    for x in qs:
        jobs += [lambda x=x: _commons_cands(x, 8 if autres else 5), lambda x=x: _openverse_cands(x, 4, 2 if autres else 1),
                 lambda x=x: _pexels_cands(x, 4 if autres else 3), lambda x=x: _pixabay_cands(x, 4 if autres else 3),
                 lambda x=x: _unsplash_cands(x, 4 if autres else 3)]
    cands = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for fut in [ex.submit(j) for j in jobs]:
            try:
                cands += fut.result()
            except Exception as e:
                print("photo : source indisponible :", e, file=sys.stderr)
    def fkey(u):  # même fichier Wikimedia en plusieurs tailles = un seul candidat
        m = re.search(r"upload\.wikimedia\.org/wikipedia/[^/]+/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/]+)", u)
        return urllib.parse.unquote(m.group(1)).lower() if m else u
    vus = {fkey(u) for u in exclure}
    cands = [c for c in list({fkey(c["url"]): c for c in reversed(cands)}.values())[::-1] if fkey(c["url"]) not in vus][:18]

    def dl(c):
        try:
            with urllib.request.urlopen(urllib.request.Request(c["url"], headers=UA), timeout=40) as r:
                data = r.read()
            if len(data) > 3000 and (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"):
                return {**c, "data": data, "mime": "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"}
        except Exception as e:
            print("photo : téléchargement impossible :", c["url"][:80], e, file=sys.stderr)
    with ThreadPoolExecutor(max_workers=6) as ex:
        cands = [c for c in ex.map(dl, cands) if c]
    cands = list({hashlib.md5(c["data"]).hexdigest(): c for c in reversed(cands)}.values())[::-1]
    if not cands:
        PHOTO_LOG.append({"nom": nom, "recherche": q, "candidats": [], "raison": "aucune image trouvée"})
        return []

    voulu = {"entreprise": "le LOGO OFFICIEL ACTUEL de cette société (net, lisible), à défaut son produit phare ou son dirigeant en portrait",
             "institution": "le LOGO ou l'EMBLÈME officiel de cette institution, à défaut le portrait de son dirigeant",
             "personne": "un PORTRAIT de cette personne, visage net et bien visible"}.get(
        typ, "une image concrète et parlante du sujet (billets pour une monnaie, grues pour l'immobilier, avion pour l'aérien)")
    parts = [{"text": (
        "Classe les meilleures images pour la couverture d'un reel d'actualité économique israélienne (affichée dans un cercle). "
        "Elle doit attirer l'œil dans le fil Instagram.\n"
        f"Sujet : « {nom} » (type : {typ}). Extrait de l'article : {contexte[:900]}\n"
        f"Ce qu'on veut : {voulu}.\n\n"
        "Règles :\n"
        "- Exactitude absolue : la bonne entité, jamais un homonyme, une autre entreprise, un autre pays ou un ancien logo "
        "quand le logo actuel est disponible. Jamais le logo ou l'emblème d'une organisation que l'article ne cite pas "
        "(par exemple « French Tech » pour la tech israélienne).\n"
        "- EXCLUS les immeubles de bureaux, façades, sièges sociaux et vues de rue : ils n'attirent pas l'œil. "
        "Un aéroport, un port, une usine, une plateforme ou un chantier EN ACTIVITÉ sont en revanche bienvenus.\n"
        "- Quand l'information porte sur une mesure (taxe, loi, réforme, taux, régulation) qui touche un secteur, "
        "l'image du SECTEUR touché (avion pour l'aérien, billets pour la monnaie, grues pour l'immobilier…) passe "
        "AVANT le logo de l'institution qui décide. Elle doit correspondre exactement : un avion de ligne civil (jamais "
        "militaire) pour des billets d'avion, de préférence israélien (El Al, Israir, Arkia, Ben Gourion), à défaut un bel "
        "avion de ligne quelconque ; de même, une image prise en Israël plutôt qu'ailleurs, à défaut une image générique "
        "réussie du secteur. Mieux vaut une image générique spectaculaire qu'aucune image du secteur.\n"
        "- L'image doit montrer LE FAIT annoncé, pas seulement le secteur : pour des licenciements, un salarié qui part "
        "avec un carton ou des bureaux vides ; pour des salaires, une fiche de paie ou de l'argent. Une photo d'événement, "
        "de conférence, de festival, de concours ou de foule sans rapport direct avec le fait est EXCLUE. Pour un sujet "
        "abstrait ou social, une belle photo d'agence (Unsplash, Pixabay, Pexels) qui illustre le fait est la meilleure option.\n"
        "- Exclus les jouets, figurines, Lego, dessins et mises en scène humoristiques : le ton du média est sérieux.\n"
        "- Exclus aussi les captures d'écran, schémas, cartes, filigranes, images floues ou mal cadrées, et les logos "
        "accompagnés de longs textes.\n"
        "- Varie les propositions : des images franchement DIFFÉRENTES entre elles (par exemple un logo, un produit, un "
        "dirigeant, une scène), jamais deux logos ni deux photos presque identiques. Inclus au moins une image concrète "
        "du secteur ou de l'activité quand il y en a une bonne.\n"
        f"Les {len(cands)} images suivent, numérotées de 0 à {len(cands) - 1}. Donne au plus {k} images, de la meilleure à la "
        "moins bonne, en indiquant pour chacune si c'est un logo (logo = true). Liste vide si aucune ne convient.")}]
    for i, c in enumerate(cands):
        parts.append({"text": f"Image {i} — {c['label'][:120]}"})
        parts.append({"inlineData": {"mimeType": c["mime"], "data": base64.b64encode(c["data"]).decode()}})
    try:
        d = first_ok(TEXT_MODELS, {"contents": [{"parts": parts}],
                                   "generationConfig": {"responseMimeType": "application/json",
                                                        "responseJsonSchema": RANK_SCHEMA}})
        out = json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought")))
    except Exception as e:
        out = {"classement": [], "raison": f"classement impossible : {e}"}
    PHOTO_LOG.append({"nom": nom, "recherche": q, "candidats": [c["label"][:80] for c in cands], **out})
    res, seen = [], set()
    for r in out.get("classement", []):
        i = r.get("image")
        if isinstance(i, int) and 0 <= i < len(cands) and i not in seen:
            seen.add(i)
            res.append({**cands[i], "logo": bool(r.get("logo")) or cands[i]["mime"] == "image/png"})
    if not res and force:  # photo obligatoire : à défaut de mieux, les premières images trouvées, sans classement
        res = [{**c, "logo": c["mime"] == "image/png"} for c in cands]
    return res[:k]


def photo_dict(c):
    return {"src": f"data:{c['mime']};base64," + base64.b64encode(c["data"]).decode(), "logo": c.get("logo", False),
            "page": c.get("label", "")[:80], "credit": c.get("credit", "")[:70]}


def _panneau(c, w, h, nom=""):
    """Une case du montage : photo recadrée plein cadre, logo entier sur fond blanc."""
    from PIL import Image, ImageOps
    import io
    im = Image.open(io.BytesIO(c["data"]))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA"); fond = Image.new("RGBA", im.size, "white"); fond.alpha_composite(im); im = fond
    im = im.convert("RGB")
    if c.get("logo") or c["mime"] == "image/png" and min(im.size) < 700:
        case = Image.new("RGB", (w, h), "white")
        im.thumbnail((int(w * .72), int(h * .4)))
        nom = "" if im.width > 2.2 * im.height else nom  # logo en toutes lettres : le nom y est déjà
        y = (h - im.height) // 2 - (40 if nom else 0)
        case.paste(im, ((w - im.width) // 2, y))
        if nom:  # le nom sous le logo : un symbole seul ne se reconnaît pas toujours
            from PIL import ImageDraw, ImageFont
            d = ImageDraw.Draw(case)
            taille = 56
            while taille > 26:
                f = ImageFont.load_default(size=taille)
                if d.textlength(nom, font=f) <= w * .8:
                    break
                taille -= 4
            d.text((w // 2, y + im.height + 40), nom, font=f, fill="#0B1B3F", anchor="mt")
        return case
    return ImageOps.fit(im, (w, h), centering=(.5, .3))


def _logo_capture(site):
    """Dernier recours pour le montage : l'icône officielle du site en 256 px (service d'icônes de Google)."""
    dom = re.sub(r"^https?://|/.*$", "", (site or "").strip().lower())
    if not dom:
        return None
    u = ("https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&size=256&url="
         + urllib.parse.quote("https://" + dom, safe=""))
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r:
        data = r.read()
    if data[1:4] != b"PNG" or int.from_bytes(data[16:20], "big") < 128:  # icône absente ou trop petite
        return None
    return {"url": u, "credit": f"Logo : {dom}", "label": f"Logo du site {dom}", "logo": True, "data": data, "mime": "image/png"}


def montage_candidat(plan, contexte, job_id):
    """Article à plusieurs acteurs (rachat, accord, duel) : un montage côte à côte de leurs logos ou portraits,
    plus parlant qu'une image d'illustration. Renvoie un candidat, ou None."""
    from concurrent.futures import ThreadPoolExecutor
    acteurs = [plan.get("entite") or {}] + (plan.get("acteurs") or [])[:2]
    acteurs = [a for a in acteurs if a.get("nom") and a.get("type") in ("entreprise", "personne", "institution")]
    if len(acteurs) < 2:
        return None
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(lambda a: (cover_candidates(a, contexte, 1) or [None])[0], acteurs))
    for i, a in enumerate(acteurs):  # rien trouvé pour une société : son logo, pris sur son site
        if not res[i] and a.get("site") and a.get("type") != "personne":
            try:
                res[i] = _logo_capture(a["site"])
            except Exception as e:
                print("capture du logo impossible :", a["site"], e, file=sys.stderr)
    paires = [(a, c) for a, c in zip(acteurs, res) if c]
    if len(paires) < 2:
        return None
    from PIL import Image, ImageDraw
    import io
    W = H = 1080; n = len(paires); larg = W // n
    img = Image.new("RGB", (W, H), "white")
    for i, (a, c) in enumerate(paires):
        img.paste(_panneau(c, larg, H, a["nom"]), (i * larg, 0))
    d = ImageDraw.Draw(img)
    for i in range(1, n):  # séparation blanche en biais, filet bleu
        x = i * larg
        d.polygon([(x - 40, H), (x + 10, H), (x + 40, 0), (x - 10, 0)], fill="white")
        d.line([(x - 15, H), (x + 15, 0)], fill="#0038B8", width=8)
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=90); data = buf.getvalue()
    tmp = Path(tempfile.mkdtemp()) / "montage.jpg"; tmp.write_bytes(data)
    import render_slides
    url = render_slides.upload_image(tmp, f"montage-{job_id}-{hashlib.md5(data).hexdigest()[:8]}")
    noms = [a["nom"] for a, _ in paires]
    credits = " · ".join(dict.fromkeys(c.get("credit", "") for _, c in paires if c.get("credit")))
    PHOTO_LOG.append({"montage": noms, "sources": [c["url"] for _, c in paires]})
    return {"url": url, "credit": credits[:120], "label": "Montage : " + " + ".join(noms), "logo": False,
            "data": data, "mime": "image/jpeg", "nom": " et ".join(noms)}


def cover_photo(ent, contexte):
    """Choix automatique (sans validation) : le premier du classement."""
    c = cover_candidates(ent, contexte, 1) or cover_candidates(ent, contexte, 1, autres=True, force=True)
    return photo_dict(c[0]) if c else None


def tts(text, out_wav):
    # Pas de consigne dans le texte : le modèle TTS la lirait à voix haute.
    spoken = text.replace("@bizhakesef", "Biz Ha Késef").replace("Bizhakesef", "Biz Ha Késef")
    body = {"contents": [{"parts": [{"text": spoken}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE}}}}}
    d = first_ok(TTS_MODELS, body)
    part = d["candidates"][0]["content"]["parts"][0]["inlineData"]
    raw = base64.b64decode(part["data"])
    if raw[:4] == b"RIFF":
        Path(out_wav).write_bytes(raw)
    else:  # PCM brut 16 bits mono 24 kHz
        with wave.open(str(out_wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(raw)


def run(args):
    return subprocess.run([FFMPEG, "-hide_banner", *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def wav_duration(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def silences(wav):
    """Pauses de la voix (début, fin) détectées par FFmpeg."""
    err = run(["-i", str(wav), "-af", "silencedetect=noise=-38dB:d=0.12", "-f", "null", "-"]).stderr
    out, start = [], None
    for line in err.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            out.append((max(0.0, start), float(line.split("silence_end:")[1].split()[0].strip("|"))))
            start = None
    return out


def boundaries(phrases, wav, dur):
    """Début de chaque phrase : la pause la plus proche de la position attendue (au prorata des caractères)."""
    sil = [(b - 0.08, b - a) for a, b in silences(wav) if a > 0.3 and b < dur - 0.2]
    total = sum(len(p) for p in phrases)
    expected, acc = [], 0
    for p in phrases[:-1]:
        acc += len(p)
        expected.append(dur * acc / total)
    m = len(expected)
    if len(sil) < m:
        return expected
    # Programmation dynamique : m pauses dans l'ordre, proches des positions attendues, les plus longues possible
    # (une fin de phrase marque une pause bien plus longue qu'une virgule).
    INF = float("inf")
    cost = lambda k, j: abs(sil[j][0] - expected[k]) - 3.0 * min(sil[j][1], 0.8)
    best = [[INF] * len(sil) for _ in range(m)]
    prev = [[-1] * len(sil) for _ in range(m)]
    for j in range(len(sil)):
        best[0][j] = cost(0, j)
    for k in range(1, m):
        for j in range(len(sil)):
            for i in range(j):
                if sil[j][0] - sil[i][0] >= 0.6 and best[k - 1][i] + cost(k, j) < best[k][j]:
                    best[k][j] = best[k - 1][i] + cost(k, j)
                    prev[k][j] = i
    j = min(range(len(sil)), key=lambda x: best[m - 1][x])
    if best[m - 1][j] == INF:
        return expected
    cuts = []
    for k in range(m - 1, -1, -1):
        cuts.append(sil[j][0])
        j = prev[k][j]
    return cuts[::-1]


def make_beat(path, dur):
    """Fond rythmé synthétisé, donc libre de droits : kick à 120 BPM, charleston, basse, nappe."""
    k, h = "mod(t,0.5)", "mod(t+0.25,0.5)"
    expr = (f"0.55*sin(2*PI*(50*{k}+3.33*(1-exp(-30*{k}))))*exp(-9*{k})"
            f"+0.10*(random(0)-0.5)*exp(-90*{h})"
            f"+0.12*sin(2*PI*55*t)*exp(-6*{h})"
            f"+0.035*(sin(2*PI*220*t)+sin(2*PI*261.63*t)+sin(2*PI*329.63*t))*(0.6+0.4*sin(2*PI*t/4))")
    run(["-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={dur + 1:.2f}",
         "-af", "lowpass=f=9000,afade=t=in:d=0.3", "-ac", "1", str(path)])


def fetch(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 BizhakesefReel/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        Path(dest).write_bytes(r.read())


def _film_chunk(args):
    """Filme les images [debut, fin) de la page dans un morceau de vidéo (exécuté en parallèle)."""
    page_file, debut, fin, out_part = args
    from playwright.sync_api import sync_playwright
    enc = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS),
                            "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                            "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS), str(out_part)],
                           stdin=subprocess.PIPE)
    with sync_playwright() as pw:
        kw = {"channel": os.environ["PW_CHANNEL"]} if os.environ.get("PW_CHANNEL") else {}
        br = pw.chromium.launch(**kw)
        page = br.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto(Path(page_file).resolve().as_uri())
        page.evaluate("window.READY")
        page.wait_for_timeout(300)
        for f in range(debut, fin):
            page.evaluate(f"render({f / FPS:.4f})")
            enc.stdin.write(page.screenshot(type="jpeg", quality=90))
        br.close()
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("encodage vidéo en échec")
    return out_part


def frames_to_video(scenes, photo, cuts, total, out_video, serie="actu"):
    """Filme reel.html image par image (rendu déterministe : render(t) fixe l'état exact de chaque image).
    Les images sont réparties entre plusieurs navigateurs en parallèle, puis les morceaux sont recollés."""
    from concurrent.futures import ProcessPoolExecutor
    html = Path(__file__).with_name("reel.html").read_text(encoding="utf-8")
    data = {"scenes": scenes, "photo": photo, "cuts": cuts, "total": total, "serie": serie}
    page_file = Path(out_video).with_suffix(".html")
    page_file.write_text(html.replace("/*DATA*/null/*END*/", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    nframes = int(total * FPS)
    k = max(1, min(int(os.environ.get("REEL_WORKERS", os.cpu_count() or 2)), 4))
    bounds = [round(i * nframes / k) for i in range(k + 1)]
    parts = [(str(page_file), bounds[i], bounds[i + 1], str(Path(out_video).with_name(f"part{i}.mp4"))) for i in range(k)]
    with ProcessPoolExecutor(max_workers=k) as ex:
        done = list(ex.map(_film_chunk, parts))
    lst = Path(out_video).with_name("parts.txt")
    lst.write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in done), encoding="utf-8")
    run(["-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out_video)])


PORTRAITS = "portraits-reel"  # {titre de couverture : {"photo": url, "garder": [slides retenues]}}, écrit par portrait.py


def portrait_info(titre):
    """Pour un portrait : la photo déjà trouvée pour la couverture et les slides à garder dans le reel."""
    try:
        import photo_choice as PC
        return (PC.load_plan(PORTRAITS) or {}).get(titre or "")
    except Exception as e:
        print("portrait : informations indisponibles :", e, file=sys.stderr)
        return None


def render(job, out):
    tmp = Path(tempfile.mkdtemp())
    info = portrait_info(job.get("titre"))
    garder = (info or {}).get("garder") or []
    if garder and len(job["slides"]) > len(garder):  # reel court : couverture, 4 faits les plus forts, fin
        job["slides"] = [job["slides"][i] for i in garder if i < len(job["slides"])]
    slides = []
    for i, src in enumerate(job["slides"]):
        p = tmp / f"s{i}.jpg"
        if src.startswith("http"):
            fetch(src, p)
        else:
            p.write_bytes(Path(src).read_bytes())
        slides.append(p)
    n = len(slides)

    import time
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    step = lambda name: print(f"[{time.time() - t0:5.1f} s] {name}", file=sys.stderr, flush=True)
    step("slides téléchargées")
    import photo_choice as PC
    saved = PC.load_plan(job["id"]) if job.get("id") and not job.get("plan") else None
    if (job.get("photo") is not None or job.get("autres")) and saved:  # le plan déjà établi est repris tel quel
        plan = {k: v for k, v in saved.items() if k in ("entite", "scenes", "retire")}
    else:
        plan = job.get("plan") or analyse(job["caption"], slides, job.get("maj", ""))
    if plan.get("retire"):  # slide dont le chiffre doublonnait l'accroche
        slides = [p for k, p in enumerate(slides) if k not in plan["retire"]]
        n = len(slides)
    step("analyse Gemini terminée")
    if job.get("mode") == "choix":  # 1er passage : l'administrateur choisit la photo sur Telegram
        vus = (saved or {}).get("photo_vus", []) if job.get("autres") else []
        cands = cover_candidates(plan.get("entite") or {}, job.get("caption", ""), 4, vus, bool(job.get("autres")))
        if not cands and not job.get("autres"):  # photo obligatoire : recherche élargie, puis sans filtre
            cands = cover_candidates(plan.get("entite") or {}, job.get("caption", ""), 4, vus, True, True)
        if info and info.get("photo") and not job.get("autres"):  # portrait : la photo de la couverture en 1er choix
            try:
                with urllib.request.urlopen(urllib.request.Request(info["photo"], headers=UA), timeout=40) as r:
                    data = r.read()
                mime = "image/png" if data[1:4] == b"PNG" else "image/jpeg"
                rest, cands = cands, [{"url": info["photo"], "credit": "Photo : Wikimedia Commons", "label": "Portrait (Wikipédia)",
                          "logo": False, "data": data, "mime": mime}]
                nomf = lambda u: urllib.parse.unquote(u.split("?")[0].rsplit("/", 1)[-1]).split("px-")[-1]
                cands += [c for c in rest if nomf(c["url"]) != nomf(info["photo"])][:3]  # pas deux fois la même photo
            except Exception as e:
                print("portrait : photo indisponible :", e, file=sys.stderr)
        if not info and not job.get("autres"):  # plusieurs acteurs : le montage en 1re proposition
            try:
                m = montage_candidat(plan, job.get("caption", ""), job["id"])
                if m:
                    cands = [m] + cands[:3]
            except Exception as e:
                print("montage impossible :", e, file=sys.stderr)
        step(f"{len(cands)} photos candidates")
        if cands:
            PC.save_plan(job["id"], {**plan, "photo_vus": vus + [c["url"] for c in cands], "photo_candidats": [
                {k: c.get(k) for k in ("url", "credit", "label", "logo", "nom")} for c in cands]})
            PC.send_choice(job["id"], job.get("titre", ""), cands)
            return {"choix": len(cands), "verification_photos": PHOTO_LOG}
        if job.get("autres"):  # rien de nouveau : on le dit, les boutons précédents restent valables
            PC.send_text("🤷 Je n'ai pas trouvé d'autres photos convenables. Choisis parmi les précédentes.")
            return {"choix": 0}
    fs = job.get("scene_maj")  # actualisation : la scène 2 affiche et dit le cours en direct, quoi que l'IA ait proposé
    if fs and len(plan["scenes"]) > 2:
        s1 = plan["scenes"][1]
        s1.update({"chiffre": fs["chiffre"], "kicker": fs["kicker"], "entite_wikipedia": ""})
        if fs["mots"] not in s1.get("phrase", "").lower():
            s1.update({"phrase": fs["phrase"], "titre": fs["titre"]})
    scenes, ent = plan["scenes"], plan.get("entite") or {}
    phrases = [s["phrase"] for s in scenes]

    def photos():  # recherche et contre-vérification des photos
        photo, choisie = None, (saved or {}).get("photo_choisie", "")
        if job.get("photo") is not None:  # numéro choisi sur Telegram (0 = sans photo)
            n, lst = int(job["photo"]), (saved or {}).get("photo_candidats", [])
            choisie = lst[n - 1] if 1 <= n <= len(lst) else None
            PC.save_plan(job["id"], {**(saved or plan), "photo_choisie": choisie})
        if choisie:  # photo validée (y compris lors d'une actualisation de dernière minute)
            with urllib.request.urlopen(urllib.request.Request(choisie["url"], headers=UA), timeout=40) as r:
                data = r.read()
            photo = photo_dict({**choisie, "data": data, "mime": "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"})
        elif choisie == "":  # aucune validation enregistrée : choix automatique
            photo = cover_photo(ent, job.get("caption", ""))
        if photo:
            photo["nom"] = (choisie or {}).get("nom") or (ent.get("nom", "") if ent.get("type", "aucune") != "aucune" else "")
        for s in scenes[1:-1]:
            w = s.get("entite_wikipedia", "")
            if w and w != ent.get("wikipedia"):
                s["photo"] = wiki_photo(w, w, s.get("titre", "") + " — " + job.get("caption", ""))
        step("photos vérifiées")
        return photo

    def voix():  # synthèse vocale, accélération, silences retirés
        raw = tmp / "voice_raw.wav"
        tts("\n\n".join(phrases), raw)
        v = tmp / "voice.wav"
        run(["-y", "-i", str(raw), "-af", f"atempo={SPEED},silenceremove=start_periods=1:start_threshold=-45dB,"
             "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse", "-ar", "48000", str(v)])
        step("voix prête")
        return v

    # Les photos et la voix ne dépendent pas l'une de l'autre : on les prépare en même temps
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_photo, f_voice = ex.submit(photos), ex.submit(voix)
        photo, voice = f_photo.result(), f_voice.result()
    (tmp / "plan.json").write_text(json.dumps({**plan, "verification_photos": PHOTO_LOG}, ensure_ascii=False, indent=1,
                                              default=lambda o: None), encoding="utf-8")
    dur_voice = wav_duration(voice)
    cuts = [LEAD + c for c in boundaries(phrases, voice, dur_voice)]
    total = LEAD + dur_voice + TAIL

    video = tmp / "video.mp4"
    serie = "portrait" if info else "savoir" if job.get("caption", "").lstrip().lower().startswith("le saviez-vous") else "actu"
    if serie != "actu":  # habillage et sur-titre de la série, reconnaissables dès la première image
        scenes[0]["kicker"] = "PORTRAIT" if serie == "portrait" else "LE SAVIEZ-VOUS ?"
    frames_to_video(scenes, photo, cuts, total, video, serie)
    step("animation filmée")

    # Son : voix + fond musical qui s'efface sous la voix + whoosh à chaque transition
    music = Path(__file__).with_name("music.mp3")
    if not music.exists():
        music = tmp / "beat.wav"
        make_beat(music, total)
    lead_ms = int(LEAD * 1000)
    filters = [f"[1:a]adelay={lead_ms}|{lead_ms},apad,atrim=0:{total:.3f},asplit=2[voice][key]",
               f"[2:a]aloop=loop=-1:size=2000000000,atrim=0:{total:.3f},volume={MUSIC_VOL},"
               f"afade=t=out:st={total - 1.0:.3f}:d=1.0[bed]",
               "[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=350[duck]"]
    mix = ["[voice]", "[duck]"]
    for j, c in enumerate(cuts):
        ms = int(max(0, c - 0.25) * 1000)
        filters.append(f"anoisesrc=d=0.5:c=pink:r=48000:a=0.6,highpass=f=400,lowpass=f=7000,"
                       f"afade=t=in:d=0.25:curve=exp,afade=t=out:st=0.25:d=0.25,volume=0.3,adelay={ms}[w{j}]")
        mix.append(f"[w{j}]")
    filters.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first,"
                   f"atrim=0:{total:.3f},loudnorm=I=-14:TP=-1.5,aformat=channel_layouts=stereo[a]")
    run(["-y", "-i", str(video), "-i", str(voice), "-i", str(music), "-filter_complex", ";".join(filters),
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)])
    step("son mixé, reel terminé")
    return {"duree": round(total, 1), "scenes": n, "coupes": [round(c, 2) for c in cuts],
            "entite": ent, "photo": photo and photo.get("page"), "phrases": phrases, "plan": str(tmp / "plan.json")}


if __name__ == "__main__":
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(render(job, sys.argv[2]), ensure_ascii=False))
