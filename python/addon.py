import asyncio
import base64
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from parse_m3u import clean_title_for_tmdb, group_content, parse_m3u

app = FastAPI(title="M3U IPTV Stremio Addon")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["*"],
)

PORT = int(os.environ.get("PORT", 7860))

CONFIG_FILE = Path(__file__).parent / "configs.json"


def load_config_store() -> dict:
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text("utf-8"))
            print(f"📂 {len(data)} configs loaded")
            return data
    except Exception as e:
        print(f"❌ Cannot load configs: {e}")
    return {}


def persist_config_store():
    try:
        CONFIG_FILE.write_text(json.dumps(config_store, indent=2, ensure_ascii=False), "utf-8")
    except Exception as e:
        print(f"❌ Cannot save configs: {e}")


config_store: dict = load_config_store()


def save_config(config: dict) -> str:
    raw = json.dumps(config, sort_keys=True)
    cfg_id = hashlib.sha256(raw.encode()).hexdigest()[:12]
    config_store[cfg_id] = config
    persist_config_store()
    return cfg_id


def get_config(cfg_id: str) -> Optional[dict]:
    return config_store.get(cfg_id)


global_data: dict = {
    "movies":            [],
    "series":            {},
    "channels":          [],
    "tmdb_cache":        {},
    "movie_imdb_index":  {},
    "series_imdb_index": {},
    "api_key":           None,
    "github_token":      None,
    "github_repo":       None,
    "config_id":         None,
    "ready":             False,
}


def normalize(s: str = "") -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"\b(19|20)\d{2}\b", "", s)
    s = re.sub(r"1080p|720p|2160p|4k|hdr|webrip|bluray|x264|x265", "", s, flags=re.IGNORECASE)
    s = re.sub(r"latino|castellano|dual|subtitulado|sub", "", s, flags=re.IGNORECASE)
    s = re.sub(r"s\d{1,2}e\d{1,2}", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^a-z0-9]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ─────────────────────────────────────────────
# CACHE TMDB EN GITHUB (opcional)
# Con un token + repo cargados desde /configure, cada resolucion de titulo
# contra TMDB se guarda ahi ademas de en memoria. Sin esto, el progreso se
# pierde entero cada vez que el contenedor se reinicia (Render en plan
# gratis lo hace solo con inactividad), y en listas grandes volver a
# resolver todo desde cero contra la API puede tardar bastante. Es
# enteramente opcional -- sin nada configurado, funciona igual que antes.
# ─────────────────────────────────────────────

GITHUB_CACHE_PATH = "tmdb-cache.json"
github_cache_sha: Optional[str] = None
github_save_lock = asyncio.Lock()


async def load_tmdb_cache_from_github(token: Optional[str], repo: Optional[str]) -> dict:
    global github_cache_sha
    if not token or not repo:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"https://api.github.com/repos/{repo}/contents/{GITHUB_CACHE_PATH}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            if res.status_code == 404:
                print("📦 Todavia no existe tmdb-cache.json en el repo, arranca vacio")
                return {}
            res.raise_for_status()
            data = res.json()
            github_cache_sha = data["sha"]
            contenido = base64.b64decode(data["content"]).decode("utf-8")
            cache = json.loads(contenido)
            if isinstance(cache, dict):
                print(f"📦 {len(cache)} resoluciones TMDB cargadas desde GitHub")
                return cache
    except Exception as e:
        print(f"⚠️ No se pudo cargar el cache de GitHub: {e}")
    return {}


async def save_tmdb_cache_to_github(token: Optional[str], repo: Optional[str], cache: dict):
    global github_cache_sha
    if not token or not repo:
        return
    async with github_save_lock:
        try:
            contenido_b64 = base64.b64encode(json.dumps(cache).encode("utf-8")).decode("ascii")
            payload = {
                "message": f"Actualizar cache TMDB ({len(cache)} entradas)",
                "content": contenido_b64,
            }
            if github_cache_sha:
                payload["sha"] = github_cache_sha
            async with httpx.AsyncClient(timeout=20) as client:
                res = await client.put(
                    f"https://api.github.com/repos/{repo}/contents/{GITHUB_CACHE_PATH}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                    json=payload,
                )
                if res.status_code == 409:
                    # el archivo cambio del lado de GitHub desde la ultima
                    # vez que leimos el sha -- se refresca y se reintenta
                    fresh = await client.get(
                        f"https://api.github.com/repos/{repo}/contents/{GITHUB_CACHE_PATH}",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                    )
                    github_cache_sha = fresh.json()["sha"]
                    payload["sha"] = github_cache_sha
                    res2 = await client.put(
                        f"https://api.github.com/repos/{repo}/contents/{GITHUB_CACHE_PATH}",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                        json=payload,
                    )
                    res2.raise_for_status()
                    github_cache_sha = res2.json()["content"]["sha"]
                    print("📦 Cache TMDB guardado en GitHub tras reintento")
                    return
                res.raise_for_status()
                github_cache_sha = res.json()["content"]["sha"]
                print(f"📦 Cache TMDB guardado en GitHub ({len(cache)} entradas)")
        except Exception as e:
            print(f"⚠️ No se pudo guardar el cache en GitHub: {e}")


async def search_tmdb(title: str, content_type: str, tmdb_cache: dict, api_key: Optional[str]) -> Optional[str]:
    if not api_key:
        return None
    clean = normalize(clean_title_for_tmdb(title))
    if clean in tmdb_cache:
        return tmdb_cache[clean]
    try:
        endpoint = "tv" if content_type == "series" else "movie"
        async with httpx.AsyncClient(timeout=10) as client:
            search_res = await client.get(
                f"https://api.themoviedb.org/3/search/{endpoint}",
                params={"api_key": api_key, "query": clean, "language": "es-MX"},
            )
            data = search_res.json()
            if not data.get("results"):
                tmdb_cache[clean] = None
                return None
            result_id = data["results"][0]["id"]
            det_res = await client.get(
                f"https://api.themoviedb.org/3/{endpoint}/{result_id}/external_ids",
                params={"api_key": api_key},
            )
            det = det_res.json()
            imdb = det.get("imdb_id")
            tmdb_cache[clean] = imdb
            return imdb
    except Exception:
        tmdb_cache[clean] = None
        return None


def chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


async def prefetch_tmdb(data: dict):
    api_key = data["api_key"]
    if not api_key:
        return

    github_token = data.get("github_token")
    github_repo  = data.get("github_repo")
    tmdb_cache   = data["tmdb_cache"]

    movie_list  = [m for m in data["movies"]          if not m.id.startswith("tt")]
    series_list = [s for s in data["series"].values() if not s.id.startswith("tt")]

    print(f"⏳ Pre-carga TMDB iniciada")
    print(f"🎬 {len(movie_list)} movies")
    print(f"📺 {len(series_list)} series")

    resolved_movies = 0
    resuelto_desde_ultimo_guardado = 0

    async def guardar_si_corresponde():
        nonlocal resuelto_desde_ultimo_guardado
        if not github_token or not github_repo:
            return
        resuelto_desde_ultimo_guardado += 1
        if resuelto_desde_ultimo_guardado >= 50:
            resuelto_desde_ultimo_guardado = 0
            await save_tmdb_cache_to_github(github_token, github_repo, tmdb_cache)

    for batch in chunks(movie_list, 4):
        async def resolve_movie(movie):
            nonlocal resolved_movies
            try:
                imdb = await search_tmdb(movie.title, "movie", tmdb_cache, api_key)
                if imdb:
                    data["movie_imdb_index"][imdb] = movie.id
                    movie.id = imdb
                    resolved_movies += 1
                await guardar_si_corresponde()
            except Exception as e:
                print(f"❌ TMDB movie error: {movie.title} — {e}")
        await asyncio.gather(*[resolve_movie(m) for m in batch])
        print(f"🎬 Movies solved: {resolved_movies}/{len(movie_list)}")
        await asyncio.sleep(0.4)

    print("✅ Movies done")

    resolved_series = 0
    for batch in chunks(series_list, 4):
        async def resolve_series(show):
            nonlocal resolved_series
            try:
                imdb = await search_tmdb(show.title, "series", tmdb_cache, api_key)
                if imdb:
                    data["series_imdb_index"][imdb] = show.id
                    show.id = imdb
                    resolved_series += 1
                await guardar_si_corresponde()
            except Exception as e:
                print(f"❌ TMDB series error: {show.title} — {e}")
        await asyncio.gather(*[resolve_series(s) for s in batch])
        print(f"📺 Series resueltas: {resolved_series}/{len(series_list)}")
        await asyncio.sleep(0.4)

    if github_token and github_repo:
        await save_tmdb_cache_to_github(github_token, github_repo, tmdb_cache)

    print("✅ Pre-load TMDB complete")


async def descargar_con_reintentos(client: httpx.AsyncClient, url: str, intentos: int = 3) -> httpx.Response:
    ultimo_error = None
    for i in range(intentos):
        try:
            res = await client.get(url, timeout=30)
            res.raise_for_status()
            return res
        except Exception as e:
            ultimo_error = e
            if i < intentos - 1:
                espera = (i + 1) * 5
                print(f"⚠️ Intento {i+1}/{intentos} fallo para {url}: {e} -- reintentando en {espera}s")
                await asyncio.sleep(espera)
    raise ultimo_error


async def load_list(m3u_urls: list[str]) -> dict:
    all_items = []
    DESCARGA_TIMEOUT_MAX_S = 150  # red de seguridad por si una lista se cuelga de verdad

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        async def descargar_una(url: str):
            try:
                print(f"📥 Downloads: {url}")
                res = await asyncio.wait_for(descargar_con_reintentos(client, url), timeout=DESCARGA_TIMEOUT_MAX_S)
                items = parse_m3u(res.text)
                print(f"📺 {len(items)} items found en {url}")
                return items
            except Exception as e:
                print(f"❌ Error downloading {url}: {e}")
                return []

        # en paralelo, no una por una -- una lista lenta no bloquea a las demas
        resultados = await asyncio.gather(*[descargar_una(u) for u in m3u_urls])
        for items in resultados:
            all_items.extend(items)

    print(f"📦 Total: {len(all_items)}")
    print("🧩 Agrupando contenido...")
    grouped = group_content(all_items)
    print(f"✅ {len(grouped['movies'])} movies")
    print(f"✅ {len(grouped['series'])} series")
    return grouped


async def init_data(config: dict, config_id: str):
    global global_data
    print("🔄 Loading lists...")
    global_data["ready"] = False

    github_token = config.get("githubToken")
    github_repo  = config.get("githubRepo")

    grouped, tmdb_cache_guardado = await asyncio.gather(
        load_list(config["m3uUrls"]),
        load_tmdb_cache_from_github(github_token, github_repo),
    )

    global_data = {
        "movies":            grouped["movies"],
        "series":            grouped["series"],
        "channels":          grouped["channels"],
        "tmdb_cache":        tmdb_cache_guardado,
        "movie_imdb_index":  {},
        "series_imdb_index": {},
        "api_key":           config.get("tmdbApiKey"),
        "github_token":      github_token,
        "github_repo":       github_repo,
        "config_id":         config_id,
        "ready":             True,
    }
    print("✅ Data loaded and ready")
    asyncio.create_task(prefetch_tmdb(global_data))


LOGO_SVG = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 256 256'>
  <defs>
    <linearGradient id='bg' x1='0%' y1='0%' x2='100%' y2='100%'>
      <stop offset='0%' stop-color='#1a1a2e'/><stop offset='100%' stop-color='#0f3460'/>
    </linearGradient>
    <linearGradient id='txt' x1='0%' y1='0%' x2='100%' y2='0%'>
      <stop offset='0%' stop-color='#00d4ff'/><stop offset='100%' stop-color='#0077ff'/>
    </linearGradient>
  </defs>
  <rect width='256' height='256' rx='48' fill='url(#bg)'/>
  <rect x='20' y='20' width='216' height='216' rx='36' fill='none' stroke='#00d4ff' stroke-width='3' stroke-opacity='0.3'/>
  <text x='128' y='168' font-family='Arial Black,sans-serif' font-size='96' font-weight='900' text-anchor='middle' fill='url(#txt)'>M3U</text>
</svg>"""

LOGO = f"data:image/svg+xml;base64,{base64.b64encode(LOGO_SVG.encode()).decode()}"

STREMIO_SIGNATURE = (
    "eyJhbGciOiJkaXIiLCJlbmMiOiJBMTI4Q0JDLUhTMjU2In0..drO4si40GNH5_7aW8jgB9g."
    "-1ysZnzhUaDVuZwvH2qcKs-pGPZ5D1ikiZQG1OfrWSNLrdVAU4wiuI1zXj2LtWNyn-ckw9K3be7"
    "ufwYrfXra0ty2W72J5wibK6spyF0n20oc925LpgsA2yhZvfYpGWeh.1RFI7MSVY2fm6IKI7dOqyw"
)


@app.get("/")
async def root():
    return RedirectResponse("/configure")


@app.get("/configure")
async def configure():
    return FileResponse(Path(__file__).parent / "configure.html")


@app.post("/api/config")
async def api_config(request: Request):
    body = await request.json()
    m3u_urls = body.get("m3uUrls")
    if not isinstance(m3u_urls, list) or not m3u_urls:
        return JSONResponse({"error": "m3uUrls required"}, status_code=400)
    config = {"m3uUrls": m3u_urls}
    if body.get("tmdbApiKey"):
        config["tmdbApiKey"] = body["tmdbApiKey"]
    if body.get("githubToken"):
        config["githubToken"] = body["githubToken"]
    if body.get("githubRepo"):
        config["githubRepo"] = body["githubRepo"]
    if body.get("showChannels"):
        config["showChannels"] = True
    cfg_id = save_config(config)
    if cfg_id != global_data.get("config_id"):
        asyncio.create_task(init_data(config, cfg_id))
    return JSONResponse({"id": cfg_id})


def build_manifest(base_url: str, configured: bool = False, show_channels: bool = False) -> dict:
    manifest = {
        "id":          "com.m3uiptv.public",
        "version":     "1.1.4",
        "name":        "M3U IPTV",
        "description": "Stream your personal M3U playlist or Xtream Codes IPTV in Stremio. Auto-resolves IMDb IDs via TMDB. By Esmequiinn",
        "logo":        "https://raw.githubusercontent.com/Esmequiinn/addon-m3u_friendly/main/logo.svg",
        "resources":   ["catalog", "stream", "meta"],
        "types":       ["movie", "series"],
        "stremioAddonsConfig": {
            "issuer":    "https://stremio-addons.net",
            "signature": STREMIO_SIGNATURE,
        },
    }
    if configured:
        manifest["logo"] = LOGO
        manifest["description"] = "Stream your personal M3U playlist or Xtream Codes IPTV in Stremio. Auto-resolves IMDb IDs via TMDB. By Esmequiinn"
        manifest["catalogs"] = [
            {
                "type": "movie", "id": "m3u_movies", "name": "My Movies",
                "extra": [{"name": "search", "isRequired": False}, {"name": "skip", "isRequired": False}],
            },
            {
                "type": "series", "id": "m3u_series", "name": "My Series",
                "extra": [{"name": "search", "isRequired": False}, {"name": "skip", "isRequired": False}],
            },
        ]
        # el catalogo de canales solo se agrega si esta persona lo activo
        # al configurar -- por defecto el addon se queda solo con
        # contenido VOD (peliculas/series), como venia siendo siempre
        if show_channels:
            manifest["types"] = ["movie", "series", "tv"]
            manifest["catalogs"].append({
                "type": "tv", "id": "m3u_channels", "name": "My Channels",
                "extra": [{"name": "search", "isRequired": False}, {"name": "skip", "isRequired": False}],
            })
        manifest["behaviorHints"] = {"configurable": True, "configureUrl": f"{base_url}/configure"}
    else:
        manifest["catalogs"] = [
            {"type": "movie",  "id": "m3u_movies", "name": "My Movies",  "extra": [{"name": "search", "isRequired": False}]},
            {"type": "series", "id": "m3u_series", "name": "My Series",  "extra": [{"name": "search", "isRequired": False}]},
        ]
        manifest["behaviorHints"] = {
            "configurable":          True,
            "configurationRequired": True,
            "configureUrl":          f"{base_url}/configure",
        }
    return manifest


@app.get("/manifest.json")
async def manifest_root(request: Request):
    base_url = f"{request.url.scheme}://{request.url.netloc}"
    return JSONResponse(build_manifest(base_url, configured=False))


@app.get("/{config_id}/manifest.json")
async def manifest_configured(config_id: str, request: Request):
    config = get_config(config_id)
    if not config:
        return JSONResponse({"error": "Config not found. Please reconfigure the addon."}, status_code=404)
    base_url = f"{request.url.scheme}://{request.url.netloc}"
    return JSONResponse(build_manifest(base_url, configured=True, show_channels=bool(config.get("showChannels"))))


@app.get("/{config_id}/catalog/{type}/{cat_id}.json")
@app.get("/{config_id}/catalog/{type}/{cat_id}/{extra}.json")
async def catalog(config_id: str, type: str, cat_id: str, extra: str = ""):
    try:
        if not get_config(config_id) or not global_data["ready"]:
            return JSONResponse({"metas": []})

        extra_params = dict(pair.split("=", 1) for pair in extra.split("&") if "=" in extra) if extra else {}
        search = normalize(extra_params["search"]) if "search" in extra_params else None
        skip   = int(extra_params.get("skip", 0))
        PAGE   = 100

        if type == "movie" and cat_id == "m3u_movies":
            results = global_data["movies"]
            if search:
                results = [m for m in results if search in normalize(m.title)]
            return JSONResponse({
                "metas": [
                    {"id": m.id, "type": "movie", "name": m.title, "poster": m.poster}
                    for m in results[skip:skip + PAGE]
                ]
            })

        if type == "series" and cat_id == "m3u_series":
            results = list(global_data["series"].values())
            if search:
                results = [s for s in results if search in normalize(s.title)]
            return JSONResponse({
                "metas": [
                    {"id": s.id, "type": "series", "name": s.title, "poster": s.poster}
                    for s in results[skip:skip + PAGE]
                ]
            })

        if type == "tv" and cat_id == "m3u_channels":
            results = global_data["channels"]
            if search:
                results = [c for c in results if search in normalize(c.title)]
            return JSONResponse({
                "metas": [
                    {"id": c.id, "type": "tv", "name": c.title, "poster": c.poster}
                    for c in results[skip:skip + PAGE]
                ]
            })

        return JSONResponse({"metas": []})
    except Exception as e:
        print(f"❌ Catalog error: {e}")
        return JSONResponse({"metas": []})


@app.get("/{config_id}/meta/{type}/{meta_id}.json")
async def meta(config_id: str, type: str, meta_id: str):
    try:
        if not get_config(config_id) or not global_data["ready"]:
            return JSONResponse({"meta": None})

        movies            = global_data["movies"]
        series            = global_data["series"]
        tmdb_cache        = global_data["tmdb_cache"]
        movie_imdb_index  = global_data["movie_imdb_index"]
        series_imdb_index = global_data["series_imdb_index"]
        api_key           = global_data["api_key"]

        if type == "movie":
            slug_key = movie_imdb_index.get(meta_id, meta_id)
            movie = (
                next((m for m in movies if m.id in (meta_id, slug_key)), None)
                or next((m for m in movies if normalize(m.title) == normalize(meta_id)), None)
            )
            if not movie:
                return JSONResponse({"meta": None})
            if not movie.id.startswith("tt"):
                imdb = await search_tmdb(movie.title, "movie", tmdb_cache, api_key)
                if imdb:
                    movie_imdb_index[imdb] = movie.id
                    movie.id = imdb
            return JSONResponse({"meta": {"id": movie.id, "type": "movie", "name": movie.title, "poster": movie.poster}})

        if type == "series":
            slug_key = series_imdb_index.get(meta_id, meta_id)
            show = (
                series.get(slug_key) or series.get(meta_id)
                or next((s for s in series.values() if normalize(s.title) == normalize(meta_id)), None)
            )
            if not show:
                return JSONResponse({"meta": None})
            if not show.id.startswith("tt"):
                imdb = await search_tmdb(show.title, "series", tmdb_cache, api_key)
                if imdb:
                    series_imdb_index[imdb] = show.id
                    show.id = imdb
            return JSONResponse({
                "meta": {
                    "id": show.id, "type": "series", "name": show.title, "poster": show.poster,
                    "videos": [
                        {"id": f"{show.id}:{ep.season}:{ep.episode}", "title": ep.title, "season": ep.season, "number": ep.episode}
                        for ep in show.episodes
                    ],
                }
            })

        if type == "tv":
            # los canales no pasan por TMDB -- no hay forma de matchear un
            # canal de tv en vivo contra una pelicula/serie, el id se
            # queda tal cual se armo al agrupar
            channel = (
                next((c for c in global_data["channels"] if c.id == meta_id), None)
                or next((c for c in global_data["channels"] if normalize(c.title) == normalize(meta_id)), None)
            )
            if not channel:
                return JSONResponse({"meta": None})
            return JSONResponse({"meta": {"id": channel.id, "type": "tv", "name": channel.title, "poster": channel.poster}})

        return JSONResponse({"meta": None})
    except Exception as e:
        print(f"❌ Meta error: {e}")
        return JSONResponse({"meta": None})


@app.get("/{config_id}/stream/{type}/{stream_id}.json")
async def stream(config_id: str, type: str, stream_id: str):
    try:
        if not get_config(config_id) or not global_data["ready"]:
            return JSONResponse({"streams": []})

        movies            = global_data["movies"]
        series            = global_data["series"]
        movie_imdb_index  = global_data["movie_imdb_index"]
        series_imdb_index = global_data["series_imdb_index"]

        if type == "movie":
            slug_key = movie_imdb_index.get(stream_id, stream_id)
            movie = (
                next((m for m in movies if m.id in (stream_id, slug_key)), None)
                or next((m for m in movies if normalize(m.title) == normalize(stream_id)), None)
            )
            if not movie:
                return JSONResponse({"streams": []})
            return JSONResponse({
                "streams": [{"url": s["url"], "name": "M3U", "title": f"Stream {i+1}"} for i, s in enumerate(movie.streams)]
            })

        if type == "series":
            parts    = stream_id.split(":")
            raw_id   = parts[0]
            season   = int(parts[1]) if len(parts) > 1 else None
            episode  = int(parts[2]) if len(parts) > 2 else None
            slug_key = series_imdb_index.get(raw_id, raw_id)
            show = (
                series.get(slug_key) or series.get(raw_id)
                or next((s for s in series.values() if normalize(s.title) == normalize(raw_id) or s.id == raw_id), None)
            )
            if not show:
                return JSONResponse({"streams": []})
            eps = [e for e in show.episodes if e.season == season and e.episode == episode]
            return JSONResponse({
                "streams": [{"url": ep.url, "name": "M3U", "title": f"Stream {i+1}"} for i, ep in enumerate(eps)]
            })

        if type == "tv":
            channel = (
                next((c for c in global_data["channels"] if c.id == stream_id), None)
                or next((c for c in global_data["channels"] if normalize(c.title) == normalize(stream_id)), None)
            )
            if not channel:
                return JSONResponse({"streams": []})
            return JSONResponse({
                "streams": [{"url": s["url"], "name": "M3U", "title": f"Canal {i+1}"} for i, s in enumerate(channel.streams)]
            })

        return JSONResponse({"streams": []})
    except Exception as e:
        print(f"❌ Stream error: {e}")
        return JSONResponse({"streams": []})


async def start_keep_alive(base_url: str):
    async def ping():
        while True:
            await asyncio.sleep(14 * 60)
            try:
                async with httpx.AsyncClient() as client:
                    await client.get(f"{base_url}/manifest.json")
                print("💓 Keep-alive OK")
            except Exception as e:
                print(f"❌ Keep-alive error: {e}")
    asyncio.create_task(ping())


@app.on_event("startup")
async def on_startup():
    print(f"🚀 M3U IPTV running in http://localhost:{PORT}")

    env_urls = []
    if os.environ.get("M3U_URLS"):
        env_urls = [u.strip() for u in os.environ["M3U_URLS"].split(",") if u.strip()]
    elif os.environ.get("M3U_URL"):
        env_urls = [os.environ["M3U_URL"].strip()]

    if env_urls:
        config = {"m3uUrls": env_urls}
        if os.environ.get("TMDB_API_KEY"):
            config["tmdbApiKey"] = os.environ["TMDB_API_KEY"]
        if os.environ.get("GITHUB_TOKEN"):
            config["githubToken"] = os.environ["GITHUB_TOKEN"]
        if os.environ.get("GITHUB_REPO"):
            config["githubRepo"] = os.environ["GITHUB_REPO"]
        if os.environ.get("SHOW_CHANNELS") == "true":
            config["showChannels"] = True
        cfg_id = save_config(config)
        await init_data(config, cfg_id)
        public_url = os.environ.get("RENDER_EXTERNAL_URL")
        if public_url:
            print(f"💓 Keep-alive started: {public_url}")
            await start_keep_alive(public_url)
        return

    if config_store:
        last_id, last_config = list(config_store.items())[-1]
        print(f"🔁 Restoring config saved ({last_id})...")
        await init_data(last_config, last_id)
        public_url = os.environ.get("RENDER_EXTERNAL_URL")
        if public_url:
            await start_keep_alive(public_url)
        return

    print("⚠️  No list configured")
    public_url = os.environ.get("RENDER_EXTERNAL_URL")
    if public_url:
        await start_keep_alive(public_url)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("addon:app", host="0.0.0.0", port=PORT, reload=False)
