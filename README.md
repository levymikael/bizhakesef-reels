# bizhakesef-reels

Montage automatique des reels Instagram de @bizhakesef, exécuté gratuitement par GitHub Actions.

1. Le scénario Make envoie un événement `render_reel` avec les slides et la légende d'un carrousel.
2. `render_reel.py` fait lire les slides par Gemini (voix off et contenu de chaque scène), récupère sur Wikipédia la photo de la société ou de la personne concernée, synthétise la voix, puis filme l'animation `reel.html` (1080 × 1920) dans Chromium et mixe le son avec FFmpeg.
3. `notify.py` envoie la vidéo chez Cloudinary et prévient le scénario, qui propose le reel sur Telegram.

Les clés sont stockées dans les secrets du dépôt, jamais dans le code.
