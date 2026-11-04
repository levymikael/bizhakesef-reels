"""Monte un reel vertical 1080x1920 à partir des slides d'un carrousel Bizhakesef.

Étapes : Gemini lit les slides et écrit une phrase de voix off par slide, synthèse vocale Gemini,
détection des pauses entre les phrases pour caler chaque slide sur SA phrase, montage FFmpeg
(effet « punch » à chaque slide, transitions rapides, whoosh, fond rythmé qui s'efface sous la voix,
barre de progression), encodage au format attendu par Instagram (H.264, AAC, 30 i/s).

Usage :
  python render_reel.py job.json sortie.mp4
job.json : {"id": "...", "slides": ["https://.../1.jpg", ...], "caption": "...", "titre": "..."}
Variables d'environnement : GEMINI_API_KEY (obligatoire), FFMPEG (chemin, par défaut « ffmpeg »).
Musique : si un fichier music.mp3 est posé à côté de ce script, il remplace le fond rythmé synthétisé.
"""
import base64, json, os, subprocess, sys, tempfile, urllib.request, wave
from pathlib import Path

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
KEY = os.environ["GEMINI_API_KEY"]
TEXT_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]
TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
VOICE = os.environ.get("REEL_VOICE", "Charon")
W, H, FPS = 1080, 1920, 30
FADE = 0.25
SPEED = 1.1        # voix légèrement accélérée
LEAD = 0.15        # la voix démarre presque immédiatement (accroche)
TAIL = 0.7
MUSIC_VOL = 0.5    # fond musical, atténué automatiquement quand la voix parle
TRANSITIONS = ["slideleft", "smoothup", "slideleft", "smoothleft", "slideup"]

PROMPT = """Tu écris la voix off d'un reel Instagram de @bizhakesef, un média d'information en français sur l'économie israélienne.
Les {N} images jointes sont les slides du reel, dans l'ordre. Écris exactement {N} phrases : la phrase n°k est lue pendant que la slide n°k est à l'écran.
- Chaque phrase dit l'information visible sur SA slide, en 7 à 15 mots. Phrases courtes, rythmées, percutantes.
- Phrase 1 : accroche forte avec le chiffre clé ou le fait le plus marquant.
- Ton de présentateur de journal économique : factuel, neutre. Pas de tutoiement, pas de question au spectateur.
- Dernière phrase exactement : « Suivez Bizhakesef pour l'actualité économique d'Israël. »
- Uniquement les faits des slides et de la légende. Nombres en toutes lettres comme ils se prononcent (« quatre virgule quatre milliards de dollars »).
- Pas d'emoji, pas de hashtag.

LÉGENDE :
"""
SCHEMA = {"type": "object", "properties": {"phrases": {"type": "array", "items": {"type": "string"}}},
          "required": ["phrases"]}


def gemini(model, body):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(), headers={"x-goog-api-key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def first_ok(models, body):
    last = None
    for m in models:
        for _ in range(3):
            try:
                return gemini(m, body)
            except Exception as e:  # 503 fréquent sur l'offre gratuite : on réessaie puis on change de modèle
                last = e
    raise RuntimeError(f"Gemini indisponible : {last}")


def narration(caption, slides):
    parts = [{"text": PROMPT.replace("{N}", str(len(slides))) + caption}]
    for p in slides:
        data = Path(p).read_bytes()
        parts.append({"inlineData": {"mimeType": "image/png" if data[:4] == b"\x89PNG" else "image/jpeg",
                                     "data": base64.b64encode(data).decode()}})
    d = first_ok(TEXT_MODELS, {"contents": [{"parts": parts}],
                               "generationConfig": {"responseMimeType": "application/json",
                                                    "responseJsonSchema": SCHEMA}})
    txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought"))
    phrases = [x.strip() for x in json.loads(txt)["phrases"] if x.strip()]
    n = len(slides)
    if len(phrases) > n:  # excédent regroupé sur l'avant-dernière slide
        phrases = phrases[:n - 2] + [" ".join(phrases[n - 2:-1]), phrases[-1]]
    while len(phrases) < n:
        phrases.insert(-1, phrases[-2])
    return phrases


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

    phrases = job.get("phrases") or narration(job["caption"], slides)
    (tmp / "narration.txt").write_text("\n".join(phrases), encoding="utf-8")
    raw = tmp / "voice_raw.wav"
    tts("\n\n".join(phrases), raw)
    # Voix accélérée, silences de début et de fin retirés
    voice = tmp / "voice.wav"
    run(["-y", "-i", str(raw), "-af", f"atempo={SPEED},silenceremove=start_periods=1:start_threshold=-45dB,"
         "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse", "-ar", "48000", str(voice)])
    dur_voice = wav_duration(voice)
    cuts = [LEAD + c for c in boundaries(phrases, voice, dur_voice)]
    total = LEAD + dur_voice + TAIL

    # Durée de chaque image : chaque transition est centrée sur le début de la phrase suivante
    edges = [0.0] + cuts + [total]
    durs = [edges[i + 1] - edges[i] + FADE for i in range(n)]
    durs[0] -= FADE / 2
    durs[-1] -= FADE / 2

    inputs, filters = [], []
    for i, (p, d) in enumerate(zip(slides, durs)):
        inputs += ["-loop", "1", "-t", f"{d:.3f}", "-i", str(p)]
        frames = max(1, int(d * FPS) + 2)
        # Effet « punch » : la slide arrive zoomée à 112 % et se pose en 0,3 s, puis avance lentement
        z = f"if(lt(on,9),1.12-0.12*sin(PI/2*on/9),1.0+0.05*(on-9)/{frames})"
        filters.append(
            f"[{i}:v]scale=1080:1350,setsar=1,"
            f"drawbox=x=830:y=1212:w=200:h=72:color=white:t=fill,"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1350:fps={FPS},"
            f"pad={W}:{H}:0:(oh-ih)/2:color=white,trim=duration={d:.3f},format=yuv420p[v{i}]")
    chain = "[v0]"
    for i in range(1, n):
        tr = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
        filters.append(f"{chain}[v{i}]xfade=transition={tr}:duration={FADE}:offset={cuts[i - 1] - FADE / 2:.3f}[x{i}]")
        chain = f"[x{i}]"
    filters.append(f"color=c=0x0038B8:s={W}x14:r={FPS}:d={total:.3f}[bar]")
    filters.append(f"{chain}[bar]overlay=x='-W+W*t/{total:.3f}':y=150:shortest=1[vout]")

    # Son : voix + fond musical qui s'efface sous la voix + whoosh à chaque transition
    music = Path(__file__).with_name("music.mp3")
    if not music.exists():
        music = tmp / "beat.wav"
        make_beat(music, total)
    ia, im = n, n + 1
    lead_ms = int(LEAD * 1000)
    filters.append(f"[{ia}:a]adelay={lead_ms}|{lead_ms},apad,atrim=0:{total:.3f},asplit=2[voice][key]")
    filters.append(f"[{im}:a]aloop=loop=-1:size=2000000000,atrim=0:{total:.3f},volume={MUSIC_VOL},"
                   f"afade=t=out:st={total - 1.0:.3f}:d=1.0[bed]")
    filters.append("[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=350[duck]")
    mix = ["[voice]", "[duck]"]
    for j, c in enumerate(cuts):
        ms = int(max(0, c - 0.22) * 1000)
        filters.append(f"anoisesrc=d=0.45:c=pink:r=48000:a=0.6,highpass=f=500,lowpass=f=6000,"
                       f"afade=t=in:d=0.22:curve=exp,afade=t=out:st=0.22:d=0.23,volume=0.22,adelay={ms}[w{j}]")
        mix.append(f"[w{j}]")
    filters.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first,"
                   f"atrim=0:{total:.3f},loudnorm=I=-14:TP=-1.5,aformat=channel_layouts=stereo[a]")
    run(["-y", *inputs, "-i", str(voice), "-i", str(music), "-filter_complex", ";".join(filters),
         "-map", "[vout]", "-map", "[a]", "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)])
    return {"duree": round(total, 1), "slides": n, "coupes": [round(c, 2) for c in cuts], "phrases": phrases}


if __name__ == "__main__":
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(render(job, sys.argv[2]), ensure_ascii=False))
