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
import base64, json, os, re, subprocess, sys, tempfile, unicodedata, urllib.parse, urllib.request, wave
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
- `titre` : le message de la scène, 50 caractères maximum. Ne recopie pas le texte de la slide : résume-le. Jamais un libellé générique comme « À retenir » ou « Baisse » : le titre énonce l'information elle-même.
- `texte` : précision utile en 85 caractères maximum, qui apporte un fait de plus (ne répète ni le titre ni le chiffre), ou "". Ici, les nombres s'écrivent en chiffres (« 185 M$ », « 1,82 $ »).
- `chiffre` : le chiffre clé tel qu'affiché, court, avec unité et signe (ex. « 670 M$ », « -45 % », « +3,2 % »), ou "".
- `tendance` : « hausse », « baisse » ou « neutre ».
- `entite_wikipedia` : si la scène parle surtout d'une autre société ou personne que l'entité principale, son titre exact sur Wikipédia en anglais ; sinon "".
La dernière scène est la scène de fin : `phrase` exactement « Suivez Bizhakesef pour l'actualité économique d'Israël. », les autres champs vides.

`entite` : la société, la personnalité ou l'institution au cœur de l'information. `nom` : son nom usuel. `wikipedia` : son titre exact sur Wikipédia en anglais (ex. « Teva Pharmaceuticals », « Bezalel Smotrich », « Bank of Israel »). `type` : « entreprise », « personne », « institution », ou « aucune » pour un sujet général.

Uniquement les faits des slides et de la légende. Chaque nombre, dit ou affiché, doit être exactement celui de la slide (si la slide affiche 670 M$, la voix dit « six cent soixante-dix millions de dollars »). Pas d'emoji, pas de hashtag.

LÉGENDE :
"""
SCENE = {"type": "object", "properties": {k: {"type": "string"} for k in
         ("phrase", "kicker", "titre", "texte", "chiffre", "tendance", "entite_wikipedia")},
         "required": ["phrase", "kicker", "titre", "texte", "chiffre", "tendance", "entite_wikipedia"]}
SCHEMA = {"type": "object", "required": ["entite", "scenes"], "properties": {
    "entite": {"type": "object", "required": ["nom", "wikipedia", "type"],
               "properties": {"nom": {"type": "string"}, "wikipedia": {"type": "string"}, "type": {"type": "string"}}},
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


def analyse(caption, slides):
    parts = [{"text": PROMPT.replace("{N}", str(len(slides))) + caption}]
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


def frames_to_video(scenes, photo, cuts, total, out_video):
    """Filme reel.html image par image (rendu déterministe : render(t) fixe l'état exact de chaque image).
    Les images sont réparties entre plusieurs navigateurs en parallèle, puis les morceaux sont recollés."""
    from concurrent.futures import ProcessPoolExecutor
    html = Path(__file__).with_name("reel.html").read_text(encoding="utf-8")
    data = {"scenes": scenes, "photo": photo, "cuts": cuts, "total": total}
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


def render(job, out):
    tmp = Path(tempfile.mkdtemp())
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
    plan = job.get("plan") or analyse(job["caption"], slides)
    step("analyse Gemini terminée")
    scenes, ent = plan["scenes"], plan.get("entite") or {}
    phrases = [s["phrase"] for s in scenes]

    def photos():  # recherche et contre-vérification des photos
        photo = None
        if ent.get("type", "aucune") != "aucune":
            photo = wiki_photo(ent.get("wikipedia"), ent.get("nom"), job.get("caption", ""))
            if photo:
                photo.update({"nom": ent.get("nom", ""), "credit": "Photo : Wikimedia Commons"})
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
    frames_to_video(scenes, photo, cuts, total, video)
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
