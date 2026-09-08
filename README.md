# Auto-post diario en X (Twitter)

Publica automáticamente cada día una imagen o video de la carpeta `media/`,
usando GitHub Actions como programador.

## 1. Consigue acceso a la API de X

1. Ve a https://developer.x.com y crea un proyecto/app.
2. Necesitas como mínimo el plan **Basic** (de pago, ronda los $100/mes)
   para poder publicar (`POST /2/tweets`) y subir media vía la API.
   El plan gratuito solo permite un número muy limitado de publicaciones
   y no siempre incluye subida de media.
3. En la configuración de tu app, activa permisos de **"Read and Write"**.
4. Genera y guarda:
   - API Key
   - API Key Secret
   - Access Token
   - Access Token Secret

   (Los Access Token deben generarse *después* de poner los permisos en
   "Read and Write", si no, quedan como solo lectura.)

## 2. Crea el repositorio en GitHub

1. Crea un repo (puede ser privado).
2. Sube estos archivos:
   - `post_daily.py`
   - `.github/workflows/daily-post.yml`
   - la carpeta `media/` con tus imágenes y videos
3. Ve a **Settings → Secrets and variables → Actions** y agrega estos secrets:
   - `TW_API_KEY`
   - `TW_API_SECRET`
   - `TW_ACCESS_TOKEN`
   - `TW_ACCESS_TOKEN_SECRET`

## 3. Sube tu contenido

Coloca tus archivos dentro de `media/`. Formatos soportados:
- Imágenes: `.jpg .jpeg .png .gif .webp`
- Videos: `.mp4 .mov`

El script publica el archivo pendiente más antiguo (orden alfabético) y
lleva registro en `posted_log.json` para no repetir contenido. Puedes
nombrar tus archivos con prefijos numéricos (`001_foto.jpg`, `002_video.mp4`)
para controlar el orden.

## 4. Personaliza el horario

En `daily-post.yml`, la línea:
```yaml
- cron: "0 14 * * *"
```
está en hora UTC. Ajusta según cuándo quieras que se publique
(ej. `"0 9 * * *"` para las 9:00 UTC).

## 5. Prueba manual

Puedes lanzar el workflow sin esperar al cron: en la pestaña **Actions**
de tu repo, selecciona "Daily Twitter Post" → **Run workflow**.

## Notas

- GitHub Actions en repos privados tiene un límite de minutos gratis al mes;
  este job es muy corto (segundos), así que no debería ser problema.
- Si se acaba el contenido de `media/`, el script simplemente no publica
  nada ese día (no falla).
- Puedes cambiar la lógica de selección de archivo en `get_next_file()`
  si prefieres orden aleatorio en vez de alfabético.
