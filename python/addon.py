import asyncio
import base64
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from parse_m3u import clean_title_for_tmdb, classify_item, group_content, parse_m3u

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



OVERRIDES_FILE = Path(__file__).parent / "overrides.json"


def load_overrides_store() -> dict:
    try:
        if OVERRIDES_FILE.exists():
            data = json.loads(OVERRIDES_FILE.read_text("utf-8"))
            print(f"📂 Overrides loaded for {len(data)} config(s)")
            return data
    except Exception as e:
        print(f"❌ Cannot load overrides: {e}")
    return {}


def persist_overrides_store():
    try:
        OVERRIDES_FILE.write_text(json.dumps(overrides_store, indent=2, ensure_ascii=False), "utf-8")
    except Exception as e:
        print(f"❌ Cannot save overrides: {e}")


overrides_store: dict = load_overrides_store()


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


def guardar_override(config_id: str, tipo: str, titulo: str, imdb_id: str):
    overrides_store.setdefault(config_id, {})
    key = f"{tipo}:{normalize(titulo)}"
    overrides_store[config_id][key] = {"tipo": tipo, "titulo": titulo, "imdbId": imdb_id}
    persist_overrides_store()


def aplicar_overrides(entry: dict):
    overrides = overrides_store.get(entry["config_id"])
    if not overrides:
        return
    for ov in overrides.values():
        tipo, titulo, imdb_id = ov["tipo"], ov["titulo"], ov["imdbId"]
        if tipo == "movie":
            movie = next((m for m in entry["movies"] if normalize(m.title) == normalize(titulo)), None)
            if movie:
                entry["movie_imdb_index"][imdb_id] = movie.id
                movie.id = imdb_id
        else:
            show_key = next(
                (k for k, s in entry["series"].items() if normalize(s.title) == normalize(titulo)), None
            )
            if show_key:
                show = entry["series"][show_key]
                entry["series_imdb_index"][imdb_id] = show.id
                show.id = imdb_id



data_store: dict = {}



GITHUB_CACHE_PATH = "tmdb-cache.json"



github_cache_sha_por_repo: dict = {}
github_save_lock_por_repo: dict = {}


def lock_de(repo: str) -> asyncio.Lock:
    if repo not in github_save_lock_por_repo:
        github_save_lock_por_repo[repo] = asyncio.Lock()
    return github_save_lock_por_repo[repo]


async def load_tmdb_cache_from_github(token: Optional[str], repo: Optional[str]) -> dict:
    if not token or not repo:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"https://api.github.com/repos/{repo}/contents/{GITHUB_CACHE_PATH}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            if res.status_code == 404:
                print(f"📦 Todavia no existe tmdb-cache.json en {repo}, arranca vacio")
                return {}
            res.raise_for_status()
            data = res.json()
            github_cache_sha_por_repo[repo] = data["sha"]
            contenido = base64.b64decode(data["content"]).decode("utf-8")
            cache = json.loads(contenido)
            if isinstance(cache, dict):
                print(f"📦 {len(cache)} resoluciones TMDB cargadas desde GitHub ({repo})")
                return cache
    except Exception as e:
        print(f"⚠️ No se pudo cargar el cache de GitHub ({repo}): {e}")
    return {}


async def save_tmdb_cache_to_github(token: Optional[str], repo: Optional[str], cache: dict):
    if not token or not repo:
        return
    async with lock_de(repo):
        try:
            contenido_b64 = base64.b64encode(json.dumps(cache).encode("utf-8")).decode("ascii")
            payload = {"message": f"Actualizar cache TMDB ({len(cache)} entradas)", "content": contenido_b64}
            sha_actual = github_cache_sha_por_repo.get(repo)
            if sha_actual:
                payload["sha"] = sha_actual
            async with httpx.AsyncClient(timeout=20) as client:
                res = await client.put(
                    f"https://api.github.com/repos/{repo}/contents/{GITHUB_CACHE_PATH}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                    json=payload,
                )
                if res.status_code == 409:
                    fresh = await client.get(
                        f"https://api.github.com/repos/{repo}/contents/{GITHUB_CACHE_PATH}",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                    )
                    github_cache_sha_por_repo[repo] = fresh.json()["sha"]
                    payload["sha"] = github_cache_sha_por_repo[repo]
                    res2 = await client.put(
                        f"https://api.github.com/repos/{repo}/contents/{GITHUB_CACHE_PATH}",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                        json=payload,
                    )
                    res2.raise_for_status()
                    github_cache_sha_por_repo[repo] = res2.json()["content"]["sha"]
                    print(f"📦 Cache TMDB guardado en GitHub tras reintento ({repo})")
                    return
                res.raise_for_status()
                github_cache_sha_por_repo[repo] = res.json()["content"]["sha"]
                print(f"📦 Cache TMDB guardado en GitHub ({repo}, {len(cache)} entradas)")
        except Exception as e:
            print(f"⚠️ No se pudo guardar el cache en GitHub ({repo}): {e}")


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


async def prefetch_tmdb(entry: dict):
    api_key = entry["api_key"]
    if not api_key:
        return

    config_id    = entry["config_id"]
    github_token = entry.get("github_token")
    github_repo  = entry.get("github_repo")
    tmdb_cache   = entry["tmdb_cache"]

    movie_list  = [m for m in entry["movies"]          if not m.id.startswith("tt")]
    series_list = [s for s in entry["series"].values() if not s.id.startswith("tt")]

    print(f"⏳ [{config_id}] Pre-carga TMDB iniciada")
    print(f"🎬 [{config_id}] {len(movie_list)} peliculas")
    print(f"📺 [{config_id}] {len(series_list)} series")

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



        if data_store.get(config_id) is not entry:
            return

        async def resolve_movie(movie):
            nonlocal resolved_movies
            try:
                imdb = await search_tmdb(movie.title, "movie", tmdb_cache, api_key)
                if imdb:
                    entry["movie_imdb_index"][imdb] = movie.id
                    movie.id = imdb
                    resolved_movies += 1
                await guardar_si_corresponde()
            except Exception as e:
                print(f"❌ [{config_id}] TMDB movie error: {movie.title} — {e}")

        await asyncio.gather(*[resolve_movie(m) for m in batch])
        print(f"🎬 [{config_id}] Peliculas resueltas: {resolved_movies}/{len(movie_list)}")
        await asyncio.sleep(0.4)

    print(f"✅ [{config_id}] Peliculas terminadas")

    resolved_series = 0
    for batch in chunks(series_list, 4):
        if data_store.get(config_id) is not entry:
            return

        async def resolve_series(show):
            nonlocal resolved_series
            try:
                imdb = await search_tmdb(show.title, "series", tmdb_cache, api_key)
                if imdb:
                    entry["series_imdb_index"][imdb] = show.id
                    show.id = imdb
                    resolved_series += 1
                await guardar_si_corresponde()
            except Exception as e:
                print(f"❌ [{config_id}] TMDB series error: {show.title} — {e}")

        await asyncio.gather(*[resolve_series(s) for s in batch])
        print(f"📺 [{config_id}] Series resueltas: {resolved_series}/{len(series_list)}")
        await asyncio.sleep(0.4)

    if github_token and github_repo:
        await save_tmdb_cache_to_github(github_token, github_repo, tmdb_cache)

    print(f"✅ [{config_id}] Pre-carga TMDB completada")


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


DESCARGA_TIMEOUT_MAX_S = 150


async def descargar_y_parsear(url: str) -> list:
    print(f"📥 Descargando: {url}")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        res = await asyncio.wait_for(descargar_con_reintentos(client, url), timeout=DESCARGA_TIMEOUT_MAX_S)
        items = parse_m3u(res.text)
        print(f"📺 {len(items)} items encontrados en {url}")
        return items


async def descargar_todas_las_listas(m3u_urls: list[str]) -> dict:
    items_por_url: dict = {}

    async def descargar_una(url: str):
        try:
            return url, await descargar_y_parsear(url)
        except Exception as e:
            print(f"❌ Error descargando {url}: {e}")
            return url, None

    resultados = await asyncio.gather(*[descargar_una(u) for u in m3u_urls])
    for url, items in resultados:
        if items is not None:
            items_por_url[url] = items
    return items_por_url


def reagrupar(items_por_url: dict) -> dict:
    all_items = [item for items in items_por_url.values() for item in items]
    print(f"📦 Total acumulado: {len(all_items)}")
    print("🧩 Agrupando contenido...")
    grouped = group_content(all_items)
    print(f"✅ {len(grouped['movies'])} peliculas")
    print(f"✅ {len(grouped['series'])} series")
    return grouped


async def init_data(config: dict, config_id: str):
    print(f"🔄 [{config_id}] Cargando listas...")

    github_token = config.get("githubToken")
    github_repo  = config.get("githubRepo")

    items_por_url, tmdb_cache_guardado = await asyncio.gather(
        descargar_todas_las_listas(config["m3uUrls"]),
        load_tmdb_cache_from_github(github_token, github_repo),
    )

    grouped = reagrupar(items_por_url)
    ahora = int(time.time() * 1000)
    last_updated = {url: ahora for url in config["m3uUrls"]}

    entry = {
        "m3uUrls":           list(config["m3uUrls"]),
        "items_por_url":     items_por_url,
        "last_updated":      last_updated,
        "movies":            grouped["movies"],
        "series":            grouped["series"],
        "channels":          grouped["channels"],
        "tmdb_cache":        tmdb_cache_guardado,
        "movie_imdb_index":  {},
        "series_imdb_index": {},
        "api_key":           config.get("tmdbApiKey"),
        "github_token":      github_token,
        "github_repo":       github_repo,
        "show_channels":     bool(config.get("showChannels")),
        "config_id":         config_id,
        "ready":             True,
    }

    aplicar_overrides(entry)
    data_store[config_id] = entry

    print(f"✅ [{config_id}] Datos cargados y listos")
    asyncio.create_task(prefetch_tmdb(entry))


async def actualizar_una_lista(config_id: str, old_url: Optional[str], new_url: str) -> dict:
    entry = data_store.get(config_id)
    if not entry:
        raise ValueError("config no encontrada")

    items = await descargar_y_parsear(new_url)

    if old_url and old_url != new_url:
        entry["items_por_url"].pop(old_url, None)
    entry["items_por_url"][new_url] = items

    if old_url:
        if old_url in entry["m3uUrls"]:
            idx = entry["m3uUrls"].index(old_url)
            entry["m3uUrls"][idx] = new_url
        elif new_url not in entry["m3uUrls"]:
            entry["m3uUrls"].append(new_url)
        if old_url != new_url:
            entry["last_updated"].pop(old_url, None)
    elif new_url not in entry["m3uUrls"]:
        entry["m3uUrls"].append(new_url)

    entry["last_updated"][new_url] = int(time.time() * 1000)

    grouped = reagrupar(entry["items_por_url"])
    entry["movies"] = grouped["movies"]
    entry["series"] = grouped["series"]
    entry["channels"] = grouped["channels"]


    entry["movie_imdb_index"] = {}
    entry["series_imdb_index"] = {}
    aplicar_overrides(entry)

    raw_config = config_store.get(config_id)
    if raw_config:
        raw_config["m3uUrls"] = list(entry["m3uUrls"])
        persist_config_store()

    asyncio.create_task(prefetch_tmdb(entry))

    return estadisticas_de(entry)


def quitar_lista(config_id: str, url: str) -> dict:
    entry = data_store.get(config_id)
    if not entry:
        raise ValueError("config no encontrada")
    if len(entry["m3uUrls"]) <= 1:
        raise ValueError("no se puede quitar la unica lista de la configuracion")

    entry["items_por_url"].pop(url, None)
    entry["m3uUrls"] = [u for u in entry["m3uUrls"] if u != url]
    entry["last_updated"].pop(url, None)

    grouped = reagrupar(entry["items_por_url"])
    entry["movies"] = grouped["movies"]
    entry["series"] = grouped["series"]
    entry["channels"] = grouped["channels"]
    entry["movie_imdb_index"] = {}
    entry["series_imdb_index"] = {}
    aplicar_overrides(entry)

    raw_config = config_store.get(config_id)
    if raw_config:
        raw_config["m3uUrls"] = list(entry["m3uUrls"])
        persist_config_store()

    return estadisticas_de(entry)


def estadisticas_de_url(entry: dict, url: str) -> dict:
    items = entry["items_por_url"].get(url, [])
    movies = series = channels = sin_clasificar = 0
    for item in items:
        tipo, _ = classify_item(item)
        if tipo == "movie":
            movies += 1
        elif tipo == "series":
            series += 1
        elif tipo == "channel":
            channels += 1
        else:
            sin_clasificar += 1
    return {
        "url": url,
        "total": len(items),
        "movies": movies, "series": series, "channels": channels, "sinClasificar": sin_clasificar,
        "lastUpdated": entry["last_updated"].get(url),
    }


def estadisticas_de(entry: dict) -> dict:
    return {
        "totales": {
            "movies":   len(entry["movies"]),
            "series":   len(entry["series"]),
            "channels": len(entry["channels"]),
        },
        "porLista": [estadisticas_de_url(entry, url) for url in entry["m3uUrls"]],
    }


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


@app.get("/{config_id}/configure")
async def configure_existente(config_id: str):
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
    if cfg_id not in data_store:
        asyncio.create_task(init_data(config, cfg_id))
    return JSONResponse({"id": cfg_id})



@app.get("/api/config/{config_id}")
async def get_config_api(config_id: str):
    raw_config = get_config(config_id)
    entry = data_store.get(config_id)
    if not raw_config or not entry:
        return JSONResponse({"error": "config not found"}, status_code=404)
    return JSONResponse({
        "configId":     config_id,
        "m3uUrls":      entry["m3uUrls"],
        "showChannels": bool(entry.get("show_channels")),
        "hasTmdbKey":   bool(entry.get("api_key")),
        "hasGithub":    bool(entry.get("github_token") and entry.get("github_repo")),
        "ready":        entry.get("ready", False),
        "stats":        estadisticas_de(entry),
    })


@app.put("/api/config/{config_id}/settings")
async def put_settings(config_id: str, request: Request):
    entry = data_store.get(config_id)
    raw_config = get_config(config_id)
    if not entry or not raw_config:
        return JSONResponse({"error": "config not found"}, status_code=404)
    body = await request.json()
    if isinstance(body.get("showChannels"), bool):
        entry["show_channels"] = body["showChannels"]
        raw_config["showChannels"] = body["showChannels"]
        persist_config_store()
    return JSONResponse({"ok": True})


@app.put("/api/config/{config_id}/list")
async def put_list(config_id: str, request: Request):
    body = await request.json()
    new_url = (body.get("newUrl") or "").strip()
    old_url = (body.get("oldUrl") or "").strip() or None
    if not new_url:
        return JSONResponse({"error": "falta newUrl"}, status_code=400)
    try:
        stats = await actualizar_una_lista(config_id, old_url, new_url)
        return JSONResponse({"ok": True, "stats": stats})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/config/{config_id}/list")
async def delete_list(config_id: str, request: Request):
    body = await request.json()
    url = body.get("url")
    if not url:
        return JSONResponse({"error": "falta url"}, status_code=400)
    try:
        stats = quitar_lista(config_id, url)
        return JSONResponse({"ok": True, "stats": stats})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/config/{config_id}/tmdb-search")
async def tmdb_search(config_id: str, q: str = "", tipo: str = "movie"):
    entry = data_store.get(config_id)
    if not entry:
        return JSONResponse({"error": "config not found"}, status_code=404)
    api_key = entry.get("api_key")
    if not api_key:
        return JSONResponse({"error": "no tmdb key configured for this config"}, status_code=400)
    q = q.strip()
    if not q:
        return JSONResponse({"error": "missing q"}, status_code=400)

    tipo = "series" if tipo == "series" else "movie"
    endpoint = "tv" if tipo == "series" else "movie"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            search_res = await client.get(
                f"https://api.themoviedb.org/3/search/{endpoint}",
                params={"api_key": api_key, "query": q, "language": "es-MX"},
            )
            search_res.raise_for_status()
            top = (search_res.json().get("results") or [])[:8]

            async def con_imdb(r):
                try:
                    det_res = await client.get(
                        f"https://api.themoviedb.org/3/{endpoint}/{r['id']}/external_ids",
                        params={"api_key": api_key},
                    )
                    det = det_res.json()
                    fecha = r.get("first_air_date") if tipo == "series" else r.get("release_date")
                    return {
                        "tmdbId": r["id"],
                        "title":  r.get("name") if tipo == "series" else r.get("title"),
                        "year":   fecha[:4] if fecha else None,
                        "poster": f"https://image.tmdb.org/t/p/w200{r['poster_path']}" if r.get("poster_path") else None,
                        "imdbId": det.get("imdb_id"),
                    }
                except Exception:
                    return None

            resultados = await asyncio.gather(*[con_imdb(r) for r in top])
            return JSONResponse({"resultados": [r for r in resultados if r and r.get("imdbId")]})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/config/{config_id}/override")
async def post_override(config_id: str, request: Request):
    entry = data_store.get(config_id)
    if not entry:
        return JSONResponse({"error": "config not found"}, status_code=404)

    body = await request.json()
    tipo    = body.get("tipo")
    titulo  = body.get("titulo")
    imdb_id = body.get("imdbId")

    if tipo not in ("movie", "series"):
        return JSONResponse({"error": "tipo debe ser movie o series"}, status_code=400)
    if not titulo or not isinstance(titulo, str):
        return JSONResponse({"error": "falta titulo"}, status_code=400)
    if not imdb_id or not re.match(r"^tt\d+$", imdb_id):
        return JSONResponse({"error": "imdbId invalido (formato esperado: tt1234567)"}, status_code=400)

    if tipo == "movie":
        existe = any(normalize(m.title) == normalize(titulo) for m in entry["movies"])
    else:
        existe = any(normalize(s.title) == normalize(titulo) for s in entry["series"].values())
    if not existe:
        return JSONResponse({"error": "no se encontro ese titulo exacto en el catalogo actual"}, status_code=404)

    guardar_override(config_id, tipo, titulo, imdb_id)
    aplicar_overrides(entry)
    return JSONResponse({"ok": True})


def build_manifest(base_url: str, config_id: Optional[str] = None, configured: bool = False, show_channels: bool = False) -> dict:
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
        if show_channels:
            manifest["types"] = ["movie", "series", "tv"]
            manifest["catalogs"].append({
                "type": "tv", "id": "m3u_channels", "name": "My Channels",
                "extra": [{"name": "search", "isRequired": False}, {"name": "skip", "isRequired": False}],
            })



        manifest["behaviorHints"] = {"configurable": True, "configureUrl": f"{base_url}/{config_id}/configure"}
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
    return JSONResponse(build_manifest(base_url, config_id=config_id, configured=True, show_channels=bool(config.get("showChannels"))))


@app.get("/{config_id}/catalog/{type}/{cat_id}.json")
@app.get("/{config_id}/catalog/{type}/{cat_id}/{extra}.json")
async def catalog(config_id: str, type: str, cat_id: str, extra: str = ""):
    try:
        entry = data_store.get(config_id)
        if not entry or not entry.get("ready"):
            return JSONResponse({"metas": []})

        extra_params = dict(pair.split("=", 1) for pair in extra.split("&") if "=" in extra) if extra else {}
        search = normalize(extra_params["search"]) if "search" in extra_params else None
        skip   = int(extra_params.get("skip", 0))
        PAGE   = 100

        if type == "movie" and cat_id == "m3u_movies":
            results = entry["movies"]
            if search:
                results = [m for m in results if search in normalize(m.title)]
            return JSONResponse({
                "metas": [
                    {"id": m.id, "type": "movie", "name": m.title, "poster": m.poster}
                    for m in results[skip:skip + PAGE]
                ]
            })

        if type == "series" and cat_id == "m3u_series":
            results = list(entry["series"].values())
            if search:
                results = [s for s in results if search in normalize(s.title)]
            return JSONResponse({
                "metas": [
                    {"id": s.id, "type": "series", "name": s.title, "poster": s.poster}
                    for s in results[skip:skip + PAGE]
                ]
            })

        if type == "tv" and cat_id == "m3u_channels":
            results = entry["channels"]
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
        entry = data_store.get(config_id)
        if not entry or not entry.get("ready"):
            return JSONResponse({"meta": None})

        movies            = entry["movies"]
        series            = entry["series"]
        tmdb_cache        = entry["tmdb_cache"]
        movie_imdb_index  = entry["movie_imdb_index"]
        series_imdb_index = entry["series_imdb_index"]
        api_key           = entry["api_key"]

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
            channel = (
                next((c for c in entry["channels"] if c.id == meta_id), None)
                or next((c for c in entry["channels"] if normalize(c.title) == normalize(meta_id)), None)
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
        entry = data_store.get(config_id)
        if not entry or not entry.get("ready"):
            return JSONResponse({"streams": []})

        movies            = entry["movies"]
        series            = entry["series"]
        movie_imdb_index  = entry["movie_imdb_index"]
        series_imdb_index = entry["series_imdb_index"]

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
                next((c for c in entry["channels"] if c.id == stream_id), None)
                or next((c for c in entry["channels"] if normalize(c.title) == normalize(stream_id)), None)
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
        asyncio.create_task(init_data(config, cfg_id))



    if config_store:
        print(f"🔁 Restaurando {len(config_store)} configuracion(es) guardada(s)...")
        for cfg_id, cfg in config_store.items():
            if cfg_id in data_store:
                continue
            asyncio.create_task(init_data(cfg, cfg_id))

    if not env_urls and not config_store:
        print("⚠️  No hay listas configuradas — configura desde /configure")

    public_url = os.environ.get("RENDER_EXTERNAL_URL")
    if public_url:
        print(f"💓 Keep-alive iniciado: {public_url}")
        await start_keep_alive(public_url)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("addon:app", host="0.0.0.0", port=PORT, reload=False)
