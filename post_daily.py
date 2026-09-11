#!/usr/bin/env python3
"""
post_daily.py

Publica automáticamente en X (Twitter) un archivo AL AZAR (imagen o video)
de la carpeta `media/`, evitando repetir el mismo archivo el mismo día
(UTC). Diseñado para correr cada 30 minutos vía GitHub Actions.

Uso:
    python post_daily.py

Requiere las siguientes variables de entorno:
    TW_API_KEY
    TW_API_SECRET
    TW_ACCESS_TOKEN
    TW_ACCESS_TOKEN_SECRET
    (opcional) TWEET_TEXT_TEMPLATE  -> texto que acompaña al tweet,
        usando {filename} para insertar el nombre del archivo sin extensión
"""

import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import tweepy

MEDIA_DIR = Path("media")
LOG_FILE = Path("posted_log.json")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTS = {".mp4", ".mov"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def days_since(date_str: str) -> int:
    """Días transcurridos desde date_str (YYYY-MM-DD) hasta hoy, en UTC."""
    try:
        posted_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 9999  # fecha inválida -> lo tratamos como "hace mucho"
    return (datetime.now(timezone.utc) - posted_date).days


def load_posted_log() -> dict:
    """
    Devuelve un dict {ruta_relativa: "YYYY-MM-DD"} con la fecha (UTC) de
    la última vez que se publicó cada archivo.

    Es tolerante a un archivo corrupto, vacío, o en un formato viejo
    (ej. una lista en vez de un dict): en cualquiera de esos casos,
    arranca con un log vacío en vez de crashear.
    """
    if not LOG_FILE.exists():
        return {}
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("posted", {})
        if isinstance(raw, dict):
            return raw
        print("[WARN] posted_log.json tenía un formato viejo/ inválido, "
              "se reinicia el registro.")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] No se pudo leer posted_log.json ({e}), se reinicia el registro.")
        return {}


def save_posted_log(posted: dict) -> None:
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"posted": posted}, f, indent=2, ensure_ascii=False, sort_keys=True)


def list_all_media() -> list[Path]:
    """Todos los archivos válidos dentro de media/, incluyendo subcarpetas."""
    return [
        p for p in MEDIA_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    ]


def file_key(p: Path) -> str:
    """Clave estable para identificar un archivo en el log (ruta relativa a media/)."""
    return str(p.relative_to(MEDIA_DIR))


REPEAT_COOLDOWN_DAYS = 30


def pick_random_file(all_files: list[Path], posted: dict) -> Path | None:
    """
    Elige al AZAR un archivo entre los que NO se hayan publicado en los
    últimos REPEAT_COOLDOWN_DAYS días. Si todos se publicaron dentro de
    ese período, devuelve None.
    """
    candidates = [
        p for p in all_files
        if days_since(posted.get(file_key(p), "2000-01-01")) >= REPEAT_COOLDOWN_DAYS
    ]
    return random.choice(candidates) if candidates else None


def build_clients():
    """
    tweepy.Client (API v2) se usa para crear el tweet.
    tweepy.API (API v1.1) se sigue necesitando para subir media,
    ya que la subida de media todavía no está 100% migrada a v2.
    """
    api_key = os.environ["TW_API_KEY"]
    api_secret = os.environ["TW_API_SECRET"]
    access_token = os.environ["TW_ACCESS_TOKEN"]
    access_token_secret = os.environ["TW_ACCESS_TOKEN_SECRET"]

    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_token_secret)
    api_v1 = tweepy.API(auth)

    client_v2 = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )
    return api_v1, client_v2


def upload_media(api_v1: tweepy.API, filepath: Path) -> str:
    is_video = filepath.suffix.lower() in VIDEO_EXTS
    if is_video:
        # "amplify_video" (en vez de "tweet_video") permite videos largos
        # en cuentas Premium. chunked=True es obligatorio para archivos
        # grandes.
        media = api_v1.media_upload(
            filename=str(filepath), chunked=True, media_category="amplify_video"
        )
    else:
        media = api_v1.media_upload(filename=str(filepath))
    return media.media_id_string


def main() -> int:
    if not MEDIA_DIR.exists():
        print(f"ERROR: no existe la carpeta '{MEDIA_DIR}'", file=sys.stderr)
        return 1

    for var in ("TW_API_KEY", "TW_API_SECRET", "TW_ACCESS_TOKEN", "TW_ACCESS_TOKEN_SECRET"):
        if not os.environ.get(var):
            print(f"ERROR: falta la variable de entorno {var}", file=sys.stderr)
            return 1

    posted = load_posted_log()
    all_files = list_all_media()
    today = today_str()
    en_cooldown = sum(
        1 for p in all_files
        if days_since(posted.get(file_key(p), "2000-01-01")) < REPEAT_COOLDOWN_DAYS
    )

    print(f"[INFO] Archivos válidos en media/: {len(all_files)}")
    print(f"[INFO] En cooldown (publicados hace menos de {REPEAT_COOLDOWN_DAYS} días): {en_cooldown}")

    if not all_files:
        print("No hay ningún archivo válido dentro de 'media/'. Nada que publicar.")
        return 0

    next_file = pick_random_file(all_files, posted)
    if next_file is None:
        print(f"Todo el contenido disponible se publicó en los últimos "
              f"{REPEAT_COOLDOWN_DAYS} días. Nada nuevo que publicar por ahora.")
        return 0

    print(f"[INFO] Publicando: {next_file}")

    try:
        api_v1, client_v2 = build_clients()
        media_id = upload_media(api_v1, next_file)

        text_template = os.environ.get("TWEET_TEXT_TEMPLATE", "")
        tweet_text = text_template.format(filename=next_file.stem) if text_template else ""

        response = client_v2.create_tweet(text=tweet_text, media_ids=[media_id])
        print("[OK] Tweet publicado:", response.data)
    except Exception as e:
        # Si algo falla al publicar, NO marcamos el archivo como publicado,
        # para que el próximo intento (en 30 min) lo vuelva a probar.
        print(f"ERROR al publicar el tweet: {e}", file=sys.stderr)
        return 1

    # Solo actualizamos el log si la publicación fue exitosa.
    posted[file_key(next_file)] = today
    save_posted_log(posted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
