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
ROTATION_FILE = Path("rotation_state.json")
YOUTUBE_LOG_FILE = Path("youtube_log.json")

# Carpetas entre las que se alterna: una publicación de la primera,
# luego una de la segunda, luego otra vez la primera, etc.
CATEGORIES = ["deltarune", "shitpost"]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".wmv", ".flv", ".webm",
    ".mkv", ".m4v", ".3gp", ".3g2", ".mpg", ".mpeg", ".ts",
}
YOUTUBE_VIDEO_EXTS = {".mp4"}  # YouTube solo recibe mp4, aunque Twitter acepte más formatos

# Título fijo por categoría para los Shorts de YouTube (en vez de usar el
# nombre del archivo, que suele ser un hash sin sentido).
YOUTUBE_TITLES = {
    "deltarune": "Deltarune #shorts #deltarune",
    "shitpost": "Shitpost #shorts #memes",
}
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


def load_rotation_state() -> str:
    """Devuelve la categoría que le toca publicar ahora. Por defecto, la primera."""
    if ROTATION_FILE.exists():
        try:
            with open(ROTATION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cat = data.get("next_category")
            if cat in CATEGORIES:
                return cat
        except (json.JSONDecodeError, OSError):
            pass
    return CATEGORIES[0]


def save_rotation_state(next_category: str) -> None:
    with open(ROTATION_FILE, "w", encoding="utf-8") as f:
        json.dump({"next_category": next_category}, f, indent=2, ensure_ascii=False)


def list_media_in_category(category: str) -> list[Path]:
    folder = MEDIA_DIR / category
    if not folder.exists():
        return []
    return [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    ]


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


def pick_next_file(posted: dict) -> tuple[Path | None, str | None]:
    """
    Elige un archivo respetando la alternancia entre CATEGORIES: primero
    intenta la categoría que le toca según rotation_state.json. Si esa
    categoría no tiene ningún candidato disponible (todo en cooldown, o
    la carpeta está vacía), cae de respaldo a la otra categoría para no
    dejar de publicar.

    Devuelve (archivo_elegido, categoría_realmente_usada), o (None, None)
    si no hay absolutamente nada disponible en ninguna de las dos.
    """
    preferred = load_rotation_state()
    other = [c for c in CATEGORIES if c != preferred][0] if len(CATEGORIES) > 1 else preferred

    for category in (preferred, other):
        files = list_media_in_category(category)
        candidates = [
            p for p in files
            if days_since(posted.get(file_key(p), "2000-01-01")) >= REPEAT_COOLDOWN_DAYS
        ]
        if candidates:
            return random.choice(candidates), category

    return None, None


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


YOUTUBE_DAILY_LIMIT_PER_CATEGORY = 3


def load_youtube_log() -> dict:
    """
    {'date': 'YYYY-MM-DD', 'counts': {'deltarune': N, 'shitpost': N},
     'last_upload_utc': ISO8601 o None}
    """
    today = today_str()
    if YOUTUBE_LOG_FILE.exists():
        try:
            with open(YOUTUBE_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                data.setdefault("last_upload_utc", None)
                data.setdefault("counts", {})
                for cat in CATEGORIES:
                    data["counts"].setdefault(cat, 0)
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"date": today, "counts": {cat: 0 for cat in CATEGORIES}, "last_upload_utc": None}


def save_youtube_log(data: dict) -> None:
    with open(YOUTUBE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def youtube_credentials_available() -> bool:
    return all(
        os.environ.get(var)
        for var in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
    )


def upload_to_youtube(filepath: Path, title: str) -> str:
    """Sube un video a YouTube como Short público. Devuelve el video_id."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": f"{title}\n\n#Shorts",
            "categoryId": "24",  # Entertainment
        },
        "status": {"privacyStatus": "public"},
    }
    media = MediaFileUpload(str(filepath), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


YOUTUBE_MIN_HOURS_BETWEEN_UPLOADS = 4


def hours_since_last_youtube_upload(yt_log: dict) -> float:
    last = yt_log.get("last_upload_utc")
    if not last:
        return 9999  # nunca se subió nada -> se permite
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return 9999
    return (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600


def maybe_upload_to_youtube(filepath: Path, category: str) -> None:
    """
    Sube a YouTube si: es un .mp4 (YouTube solo recibe ese formato aquí),
    hay credenciales, esa categoría ('deltarune'/'shitpost') no llegó a
    su cupo diario de YOUTUBE_DAILY_LIMIT_PER_CATEGORY, y ya pasaron al
    menos YOUTUBE_MIN_HOURS_BETWEEN_UPLOADS horas desde la última subida
    (para repartirlas a lo largo del día en vez de subirlas todas seguidas).
    """
    if filepath.suffix.lower() not in YOUTUBE_VIDEO_EXTS:
        print(f"[INFO] YouTube: solo se suben archivos {YOUTUBE_VIDEO_EXTS}, se omite.")
        return

    if not youtube_credentials_available():
        print("[INFO] YouTube: faltan credenciales (YT_CLIENT_ID / YT_CLIENT_SECRET / "
              "YT_REFRESH_TOKEN), se omite la subida.")
        return

    yt_log = load_youtube_log()
    current_count = yt_log["counts"].get(category, 0)

    if current_count >= YOUTUBE_DAILY_LIMIT_PER_CATEGORY:
        print(f"[INFO] YouTube: cupo diario de '{category}' alcanzado "
              f"({current_count}/{YOUTUBE_DAILY_LIMIT_PER_CATEGORY}), se omite.")
        return

    hours_since = hours_since_last_youtube_upload(yt_log)
    if hours_since < YOUTUBE_MIN_HOURS_BETWEEN_UPLOADS:
        faltan = YOUTUBE_MIN_HOURS_BETWEEN_UPLOADS - hours_since
        print(f"[INFO] YouTube: última subida hace {hours_since:.1f}h, "
              f"faltan {faltan:.1f}h para la próxima. Se omite por ahora.")
        return

    try:
        title = YOUTUBE_TITLES.get(category, category)
        video_id = upload_to_youtube(filepath, title)
        print(f"[OK] Subido a YouTube ({category}): https://youtu.be/{video_id}")
        yt_log["counts"][category] = current_count + 1
        yt_log["last_upload_utc"] = datetime.now(timezone.utc).isoformat()
        save_youtube_log(yt_log)
    except Exception as e:
        print(f"[WARN] Falló la subida a YouTube (no afecta al post de Twitter): {e}")



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
    for cat in CATEGORIES:
        print(f"[INFO]   - {cat}/: {len(list_media_in_category(cat))} archivo(s)")
    print(f"[INFO] Le toca a la categoría: {load_rotation_state()}")

    if not all_files:
        print("No hay ningún archivo válido dentro de 'media/'. Nada que publicar.")
        return 0

    next_file, used_category = pick_next_file(posted)
    if next_file is None:
        print(f"Todo el contenido disponible (en ambas carpetas) se publicó en los "
              f"últimos {REPEAT_COOLDOWN_DAYS} días. Nada nuevo que publicar por ahora.")
        return 0

    print(f"[INFO] Publicando ({used_category}): {next_file}")

    try:
        api_v1, client_v2 = build_clients()
        media_id = upload_media(api_v1, next_file)

        text_template = os.environ.get("TWEET_TEXT_TEMPLATE", "")
        tweet_text = text_template.format(filename=next_file.stem) if text_template else ""

        response = client_v2.create_tweet(text=tweet_text, media_ids=[media_id])
        print("[OK] Tweet publicado:", response.data)
        maybe_upload_to_youtube(next_file, used_category)
    except Exception as e:
        # Si algo falla al publicar, NO marcamos el archivo como publicado,
        # para que el próximo intento (en 30 min) lo vuelva a probar.
        print(f"ERROR al publicar el tweet: {e}", file=sys.stderr)
        return 1

    # Solo actualizamos el log si la publicación fue exitosa.
    posted[file_key(next_file)] = today
    save_posted_log(posted)

    # La próxima vez le toca a la otra categoría (alternancia).
    next_category = [c for c in CATEGORIES if c != used_category][0] if len(CATEGORIES) > 1 else used_category
    save_rotation_state(next_category)

    return 0


if __name__ == "__main__":
    sys.exit(main())
