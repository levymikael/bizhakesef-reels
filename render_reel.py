"""Monte un reel vertical 1080x1920 à partir des slides d'un carrousel Bizhakesef.

Étapes : texte de voix off écrit par Gemini, synthèse vocale Gemini, montage FFmpeg
(slides 4:5 centrées dans un cadre aux couleurs du compte, léger zoom, fondus), encodage
au format attendu par Instagram (H.264, AAC, 30 i/s).

Usage :
  python render_reel.py job.json sortie.mp4
job.json : {"id": "...", "slides": ["https://.../1.jpg", ...], "caption": "...", "titre": "..."}
Variables d'environnement : GEMINI_API_KEY (obligatoire), FFMPEG (chemin, par défaut « ffmpeg »).
"""
import base64, json, os, subprocess, sys, tempfile, urllib.request, wave
from pathlib import Path

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
KEY = os.environ["GEMINI_API_KEY"]
TEXT_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]
TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
VOICE = os.environ.get("REEL_VOICE", "Charon")
W, H, FPS = 1080, 1920, 30
FADE = 0.4

PROMPT = """Tu écris la voix off d'un reel Instagram de @bizhakesef, un média d'information en français sur l'économie israélienne.
À partir de la légende ci-dessous, écris un texte de 50 à 65 mots, lu en 20 à 25 secondes.
- Ton de présentateur de journal économique : factuel, neutre, clair. Pas de tutoiement, pas de question au spectateur.
- Première phrase : l'information principale avec son chiffre clé.
- Ensuite deux ou trois faits, puis une phrase de contexte.
- Dernière phrase exactement : « Suivez Bizhakesef pour l'actualité économique d'Israël. »
- Utilise uniquement les faits de la légende. Écris les nombres en toutes lettres comme ils se prononcent (« quatre virgule quatre milliards de dollars »).
- Pas d'emoji, pas de hashtag, pas de titre. Réponds uniquement par le texte à lire.

LÉGENDE :
"""


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


def narration(caption):
    d = first_ok(TEXT_MODELS, {"contents": [{"parts": [{"text": PROMPT + caption}]}]})
    return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought")).strip()


def tts(text, out_wav):
    body = {"contents": [{"parts": [{"text": "Lis ce texte en français, avec le ton posé et dynamique d'un présentateur de journal économique : " + text}]}],
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
    with wave.open(str(out_wav)) as w:
        return w.getnframes() / w.getframerate()


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

    text = job.get("narration") or narration(job["caption"])
    (tmp / "narration.txt").write_text(text, encoding="utf-8")
    voice = tmp / "voice.wav"
    dur_voice = tts(text, voice)

    n = len(slides)
    # Durée par slide calée sur la voix (toute la voix doit tenir), entre 2,2 et 7 s ; la couverture reste un peu plus longtemps
    per = max(2.2, min(7.0, (dur_voice + 1.5 + FADE * (n - 1) - 0.6) / n))
    durs = [per + 0.6 if i == 0 else per for i in range(n)]
    total = sum(durs) - FADE * (n - 1)

    inputs, filters = [], []
    for i, (p, d) in enumerate(zip(slides, durs)):
        inputs += ["-loop", "1", "-t", f"{d:.3f}", "-i", str(p)]
        frames = int(d * FPS)
        # Fond blanc, slide 4:5 centrée (elle porte déjà les bandes du drapeau), zoom très lent
        filters.append(
            f"[{i}:v]scale=1080:1350,setsar=1,"
            f"zoompan=z='min(1.0+0.0006*on,1.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1350:fps={FPS},"
            f"pad={W}:{H}:0:(oh-ih)/2:color=white,"
            f"format=yuv420p[v{i}]")
    chain, offset = "[v0]", 0.0
    for i in range(1, n):
        offset += durs[i - 1] - FADE
        filters.append(f"{chain}[v{i}]xfade=transition=fade:duration={FADE}:offset={offset:.3f}[x{i}]")
        chain = f"[x{i}]"
    filters.append(f"[{n}:a]adelay=500|500,apad,atrim=0:{total:.3f},afade=t=out:st={max(0, total - 0.8):.3f}:d=0.8[a]")
    cmd = [FFMPEG, "-y", *inputs, "-i", str(voice), "-filter_complex", ";".join(filters),
           "-map", chain, "-map", "[a]", "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
           "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return {"duree": round(total, 1), "voix": round(dur_voice, 1), "slides": n, "narration": text}


if __name__ == "__main__":
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(render(job, sys.argv[2]), ensure_ascii=False))
