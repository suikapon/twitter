#!/usr/bin/env python3
"""
get_youtube_token.py

Este script se corre UNA SOLA VEZ, en tu propia PC (no en GitHub Actions),
para obtener el "refresh token" que necesita post_daily.py para subir
videos a YouTube en tu nombre.

Requisitos previos:
    1. pip install google-auth-oauthlib
    2. Tener descargado el archivo 'client_secret.json' desde Google Cloud
       Console (ver instrucciones que te dio Claude).

Uso:
    python get_youtube_token.py

Se abrirá tu navegador pidiéndote iniciar sesión con la cuenta de Google
dueña del canal de YouTube, y aceptar los permisos. Al terminar, este
script imprime el CLIENT_ID, CLIENT_SECRET y REFRESH_TOKEN que debes
guardar como Secrets en GitHub (YT_CLIENT_ID, YT_CLIENT_SECRET,
YT_REFRESH_TOKEN).
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS_FILE = "client_secret.json"


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n" + "=" * 60)
    print("¡Listo! Guarda estos 3 valores como Secrets en GitHub:")
    print("=" * 60)
    print(f"YT_CLIENT_ID={creds.client_id}")
    print(f"YT_CLIENT_SECRET={creds.client_secret}")
    print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)


if __name__ == "__main__":
    main()
