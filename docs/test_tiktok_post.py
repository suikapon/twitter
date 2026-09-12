#!/usr/bin/env python3
"""
test_tiktok_post.py

Script de PRUEBA para grabar el video demo que pide TikTok en la
revisión de la app. Corre localmente en tu PC (no en GitHub Actions).

Hace el flujo completo:
1. Abre el navegador para que autorices la app (OAuth2 + PKCE).
2. Captura el código de autorización en un servidor local.
3. Lo intercambia por un access_token.
4. Sube un video de prueba usando el Content Posting API (sandbox).

Antes de correrlo:
    pip install requests

Y en tu app de TikTok Developer, agrega este Redirect URI exacto:
    https://suikapon.github.io/twitter/callback.html

Configura tus datos abajo en CLIENT_KEY, CLIENT_SECRET, y VIDEO_PATH.
"""

import base64
import hashlib
import os
import secrets
import urllib.parse
import webbrowser

import requests

CLIENT_KEY = "PON_AQUI_TU_CLIENT_KEY"
CLIENT_SECRET = "PON_AQUI_TU_CLIENT_SECRET"
REDIRECT_URI = "https://suikapon.github.io/twitter/callback.html"
VIDEO_PATH = "video_de_prueba.mp4"  # un mp4 corto de tu carpeta media/

SCOPE = "video.publish"


def generate_pkce_pair():
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def get_authorization_code(code_challenge: str) -> str:
    params = {
        "client_key": CLIENT_KEY,
        "scope": SCOPE,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": secrets.token_urlsafe(16),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(params)
    print(f"Abriendo navegador para autorizar:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("Después de autorizar, la página te mostrará un código.")
    code = input("Pega aquí el código y presiona Enter: ").strip()
    return code


def exchange_code_for_token(code: str, code_verifier: str) -> str:
    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    print("Respuesta de token:", data)
    return data["access_token"]


def upload_video(access_token: str, video_path: str):
    file_size = os.path.getsize(video_path)

    init_resp = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "post_info": {
                "title": "Test post from AutoPoster Reiko (sandbox demo)",
                "privacy_level": "SELF_ONLY",
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": file_size,
                "total_chunk_count": 1,
            },
        },
    )
    init_resp.raise_for_status()
    init_data = init_resp.json()
    print("Respuesta init:", init_data)

    upload_url = init_data["data"]["upload_url"]

    with open(video_path, "rb") as f:
        video_data = f.read()

    upload_resp = requests.put(
        upload_url,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
        },
        data=video_data,
    )
    upload_resp.raise_for_status()
    print(f"[OK] Video subido correctamente. Status: {upload_resp.status_code}")


def main():
    verifier, challenge = generate_pkce_pair()
    code = get_authorization_code(challenge)
    if not code:
        print("No se recibió código de autorización.")
        return

    print(f"Código recibido: {code}\n")
    access_token = exchange_code_for_token(code, verifier)
    upload_video(access_token, VIDEO_PATH)


if __name__ == "__main__":
    main()
