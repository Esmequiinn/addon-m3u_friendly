const express = require("express");
const path    = require("path");
const crypto  = require("crypto");
const fs      = require("fs");
const dns     = require("dns");
const http    = require("http");
const https   = require("https");
const axios   = require("axios");
const { parseM3U, groupContent, cleanTitleForTMDB, classifyItem } = require("./parse-m3u");

const app  = express();
const PORT = process.env.PORT || 7000;

app.use(express.json());
app.use((req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "*");
  next();
});

function lookupIPv4(hostname, options, callback) {
  return dns.lookup(hostname, { ...options, family: 4 }, callback);
}
axios.defaults.httpAgent  = new http.Agent({ keepAlive: true, lookup: lookupIPv4 });
axios.defaults.httpsAgent = new https.Agent({ keepAlive: true, lookup: lookupIPv4 });


const CONFIG_FILE = path.join(__dirname, "configs.json");

function loadConfigStore() {
  try {
    if (fs.existsSync(CONFIG_FILE)) {
      const data = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
      console.log(`📂 ${Object.keys(data).length} configs cargadas desde disco`);
      return new Map(Object.entries(data));
    }
  } catch (err) {
    console.error("❌ Error cargando configs desde disco:", err.message);
  }
  return new Map();
}

function persistConfigStore() {
  try {
    const obj = Object.fromEntries(configStore);
    fs.writeFileSync(CONFIG_FILE, JSON.stringify(obj, null, 2), "utf8");
  } catch (err) {
    console.error("❌ Error guardando configs en disco:", err.message);
  }
}

const configStore = loadConfigStore();

function saveConfig(config) {
  const id = crypto
    .createHash("sha256")
    .update(JSON.stringify(config))
    .digest("hex")
    .slice(0, 12);
  configStore.set(id, config);
  persistConfigStore();
  return id;
}

function getConfig(id) {
  return configStore.get(id) || null;
}


const OVERRIDES_FILE = path.join(__dirname, "overrides.json");

function loadOverridesStore() {
  try {
    if (fs.existsSync(OVERRIDES_FILE)) {
      const data = JSON.parse(fs.readFileSync(OVERRIDES_FILE, "utf8"));
      console.log(`📂 Overrides cargados para ${Object.keys(data).length} config(s)`);
      return data;
    }
  } catch (err) {
    console.error("❌ Error cargando overrides desde disco:", err.message);
  }
  return {};
}

function persistOverridesStore() {
  try {
    fs.writeFileSync(OVERRIDES_FILE, JSON.stringify(overridesStore, null, 2), "utf8");
  } catch (err) {
    console.error("❌ Error guardando overrides en disco:", err.message);
  }
}

const overridesStore = loadOverridesStore();

function normalize(str = "") {
  return str
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\b(19|20)\d{2}\b/g, "")
    .replace(/1080p|720p|2160p|4k|hdr|webrip|bluray|x264|x265/gi, "")
    .replace(/latino|castellano|dual|subtitulado|sub/gi, "")
    .replace(/s\d{1,2}e\d{1,2}/gi, "")
    .replace(/[^a-z0-9]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function guardarOverride(configId, tipo, titulo, imdbId) {
  if (!overridesStore[configId]) overridesStore[configId] = {};
  const key = `${tipo}:${normalize(titulo)}`;
  overridesStore[configId][key] = { tipo, titulo, imdbId };
  persistOverridesStore();
}

function aplicarOverrides(entry) {
  const overrides = overridesStore[entry.configId];
  if (!overrides) return;
  for (const key of Object.keys(overrides)) {
    const { tipo, titulo, imdbId } = overrides[key];
    if (tipo === "movie") {
      const movie = entry.movies.find(m => normalize(m.title) === normalize(titulo));
      if (movie) { entry.movieImdbIndex[imdbId] = movie.id; movie.id = imdbId; }
    } else {
      const showKey = Object.keys(entry.series).find(
        k => normalize(entry.series[k].title) === normalize(titulo)
      );
      if (showKey) {
        const show = entry.series[showKey];
        entry.seriesImdbIndex[imdbId] = show.id;
        show.id = imdbId;
      }
    }
  }
}


const dataStore = new Map();

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}


const GITHUB_CACHE_PATH = "tmdb-cache.json";

const githubCacheShaPorRepo = new Map();
const githubSavePromisePorRepo = new Map();

async function loadTmdbCacheFromGithub(token, repo) {
  if (!token || !repo) return {};
  try {
    const res = await axios.get(
      `https://api.github.com/repos/${repo}/contents/${GITHUB_CACHE_PATH}`,
      { headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" }, timeout: 15000 }
    );
    githubCacheShaPorRepo.set(repo, res.data.sha);
    const contenido = Buffer.from(res.data.content, "base64").toString("utf8");
    const datos = JSON.parse(contenido);
    if (datos && typeof datos === "object") {
      console.log(`📦 ${Object.keys(datos).length} resoluciones TMDB cargadas desde GitHub (${repo})`);
      return datos;
    }
  } catch (err) {
    if (err.response?.status === 404) {
      console.log(`📦 Todavia no existe tmdb-cache.json en ${repo}, arranca vacio`);
    } else {
      console.warn(`⚠️ No se pudo cargar el cache de GitHub (${repo}):`, err.message);
    }
  }
  return {};
}

async function saveTmdbCacheToGithub(token, repo, cache) {
  if (!token || !repo) return;
  if (githubSavePromisePorRepo.has(repo)) return githubSavePromisePorRepo.get(repo);
  const promesa = (async () => {
    try {
      const contenidoB64 = Buffer.from(JSON.stringify(cache), "utf8").toString("base64");
      const shaActual = githubCacheShaPorRepo.get(repo);
      const res = await axios.put(
        `https://api.github.com/repos/${repo}/contents/${GITHUB_CACHE_PATH}`,
        {
          message: `Actualizar cache TMDB (${Object.keys(cache).length} entradas)`,
          content: contenidoB64,
          ...(shaActual ? { sha: shaActual } : {})
        },
        { headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" }, timeout: 20000 }
      );
      githubCacheShaPorRepo.set(repo, res.data.content.sha);
      console.log(`📦 Cache TMDB guardado en GitHub (${repo}, ${Object.keys(cache).length} entradas)`);
    } catch (err) {
      if (err.response?.status === 409) {
        try {
          const fresh = await axios.get(
            `https://api.github.com/repos/${repo}/contents/${GITHUB_CACHE_PATH}`,
            { headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" }, timeout: 15000 }
          );
          githubCacheShaPorRepo.set(repo, fresh.data.sha);
          const contenidoB64 = Buffer.from(JSON.stringify(cache), "utf8").toString("base64");
          const res2 = await axios.put(
            `https://api.github.com/repos/${repo}/contents/${GITHUB_CACHE_PATH}`,
            { message: `Actualizar cache TMDB (${Object.keys(cache).length} entradas)`, content: contenidoB64, sha: githubCacheShaPorRepo.get(repo) },
            { headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" }, timeout: 20000 }
          );
          githubCacheShaPorRepo.set(repo, res2.data.content.sha);
          console.log(`📦 Cache TMDB guardado en GitHub tras reintento (${repo})`);
        } catch (err2) {
          console.warn(`⚠️ No se pudo guardar el cache en GitHub ni tras reintentar (${repo}):`, err2.message);
        }
      } else {
        console.warn(`⚠️ No se pudo guardar el cache en GitHub (${repo}):`, err.message);
      }
    }
  })();
  githubSavePromisePorRepo.set(repo, promesa);
  try {
    await promesa;
  } finally {
    githubSavePromisePorRepo.delete(repo);
  }
}

async function searchTMDB(title, type, tmdbCache, apiKey) {
  if (!apiKey) return null;
  const clean = normalize(cleanTitleForTMDB(title));
  if (clean in tmdbCache) return tmdbCache[clean];
  try {
    const endpoint  = type === "series" ? "tv" : "movie";
    const searchRes = await fetch(
      `https://api.themoviedb.org/3/search/${endpoint}?api_key=${apiKey}&query=${encodeURIComponent(clean)}&language=es-MX`
    );
    const data = await searchRes.json();
    if (!data.results?.length) { tmdbCache[clean] = null; return null; }
    const detRes = await fetch(
      `https://api.themoviedb.org/3/${endpoint}/${data.results[0].id}/external_ids?api_key=${apiKey}`
    );
    const det  = await detRes.json();
    const imdb = det.imdb_id || null;
    tmdbCache[clean] = imdb;
    return imdb;
  } catch {
    tmdbCache[clean] = null;
    return null;
  }
}

function chunks(arr, size) {
  const result = [];
  for (let i = 0; i < arr.length; i += size) result.push(arr.slice(i, i + size));
  return result;
}

async function prefetchTMDB(entry) {
  const { movies, series, tmdbCache, movieImdbIndex, seriesImdbIndex, apiKey, githubToken, githubRepo, configId } = entry;
  if (!apiKey) return;

  const movieList  = movies.filter(m => !m.id.startsWith("tt"));
  const seriesList = Object.values(series).filter(s => !s.id.startsWith("tt"));

  console.log(`⏳ [${configId}] Pre-carga TMDB iniciada`);
  console.log(`🎬 [${configId}] ${movieList.length} películas`);
  console.log(`📺 [${configId}] ${seriesList.length} series`);

  let resolvedMovies = 0;
  let resolvedSeries = 0;
  let entriesSinceLastSave = 0;

  async function guardarSiCorresponde() {
    if (!githubToken || !githubRepo) return;
    entriesSinceLastSave++;
    if (entriesSinceLastSave >= 50) {
      entriesSinceLastSave = 0;
      await saveTmdbCacheToGithub(githubToken, githubRepo, tmdbCache);
    }
  }

  for (const batch of chunks(movieList, 4)) {
    if (dataStore.get(configId) !== entry) return;
    await Promise.all(batch.map(async movie => {
      try {
        const imdb = await searchTMDB(movie.title, "movie", tmdbCache, apiKey);
        if (imdb) { movieImdbIndex[imdb] = movie.id; movie.id = imdb; resolvedMovies++; }
        await guardarSiCorresponde();
      } catch (err) {
        console.error(`❌ [${configId}] TMDB movie error: ${movie.title}`);
      }
    }));
    console.log(`🎬 [${configId}] Películas resueltas: ${resolvedMovies}/${movieList.length}`);
    await sleep(400);
  }

  console.log(`✅ [${configId}] Películas terminadas`);

  for (const batch of chunks(seriesList, 4)) {
    if (dataStore.get(configId) !== entry) return;
    await Promise.all(batch.map(async show => {
      try {
        const imdb = await searchTMDB(show.title, "series", tmdbCache, apiKey);
        if (imdb) { seriesImdbIndex[imdb] = show.id; show.id = imdb; resolvedSeries++; }
        await guardarSiCorresponde();
      } catch (err) {
        console.error(`❌ [${configId}] TMDB series error: ${show.title}`);
      }
    }));
    console.log(`📺 [${configId}] Series resueltas: ${resolvedSeries}/${seriesList.length}`);
    await sleep(400);
  }

  if (githubToken && githubRepo) {
    await saveTmdbCacheToGithub(githubToken, githubRepo, tmdbCache);
  }

  console.log(`✅ [${configId}] Pre-carga TMDB completada`);
}

async function descargarConReintentos(url, intentos = 3) {
  let ultimoError;
  for (let i = 0; i < intentos; i++) {
    try {
      return await axios.get(url, { timeout: 30000, responseType: "text" });
    } catch (e) {
      const esErrorCert = /certificate|SSL|TLS/i.test(e.message || "");
      if (esErrorCert) {
        try {
          return await axios.get(url, {
            timeout: 30000,
            responseType: "text",
            httpsAgent: new https.Agent({ keepAlive: true, lookup: lookupIPv4, rejectUnauthorized: false })
          });
        } catch (e2) {
          ultimoError = e2;
        }
      } else {
        ultimoError = e;
      }
      if (i < intentos - 1) {
        const esperaMs = (i + 1) * 5000;
        console.warn(`⚠️ Intento ${i + 1}/${intentos} fallo para ${url}: ${(ultimoError||e).message} -- reintentando en ${esperaMs / 1000}s`);
        await sleep(esperaMs);
      }
    }
  }
  throw ultimoError;
}

function conTechoDeTiempo(promise, ms, etiqueta) {
  let venceTimer;
  const timeout = new Promise((_, reject) => {
    venceTimer = setTimeout(() => reject(new Error(`timeout duro de ${ms}ms superado (${etiqueta})`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(venceTimer));
}

const DESCARGA_TIMEOUT_MAX_MS = 150000;

async function descargarYParsear(url) {
  console.log(`📥 Descargando: ${url}`);
  const res = await conTechoDeTiempo(descargarConReintentos(url), DESCARGA_TIMEOUT_MAX_MS, url);
  const items = parseM3U(res.data);
  console.log(`📺 ${items.length} items encontrados en ${url}`);
  return items;
}

async function descargarTodasLasListas(m3uUrls) {
  const itemsPorUrl = {};
  const resultados = await Promise.allSettled(
    m3uUrls.map(async url => ({ url, items: await descargarYParsear(url) }))
  );
  for (const r of resultados) {
    if (r.status === "fulfilled") {
      itemsPorUrl[r.value.url] = r.value.items;
    } else {
      console.error(`❌ Error descargando una lista:`, r.reason?.message || r.reason);
    }
  }
  return itemsPorUrl;
}

function reagrupar(itemsPorUrl) {
  const allItems = Object.values(itemsPorUrl).flat();
  console.log(`📦 Total acumulado: ${allItems.length}`);
  console.log(`🧩 Agrupando contenido...`);
  const grouped = groupContent(allItems);
  console.log(`✅ ${grouped.movies.length} películas`);
  console.log(`✅ ${Object.keys(grouped.series).length} series`);
  return grouped;
}

async function initData(config, configId) {
  console.log(`🔄 [${configId}] Cargando listas...`);

  const githubToken = config.githubToken || null;
  const githubRepo  = config.githubRepo || null;

  const [itemsPorUrl, tmdbCacheGuardado] = await Promise.all([
    descargarTodasLasListas(config.m3uUrls),
    loadTmdbCacheFromGithub(githubToken, githubRepo)
  ]);

  const { movies, series, channels } = reagrupar(itemsPorUrl);
  const ahora = Date.now();
  const lastUpdated = {};
  for (const url of config.m3uUrls) lastUpdated[url] = ahora;

  const entry = {
    m3uUrls:         [...config.m3uUrls],
    itemsPorUrl,
    lastUpdated,
    movies,
    series,
    channels,
    tmdbCache:       tmdbCacheGuardado,
    movieImdbIndex:  {},
    seriesImdbIndex: {},
    apiKey:          config.tmdbApiKey || null,
    githubToken,
    githubRepo,
    showChannels:    !!config.showChannels,
    configId,
    ready:           true
  };

  aplicarOverrides(entry);
  dataStore.set(configId, entry);

  console.log(`✅ [${configId}] Datos cargados y listos`);
  prefetchTMDB(entry).catch(err =>
    console.error(`❌ [${configId}] Error en pre-carga TMDB:`, err)
  );
}

async function actualizarUnaLista(configId, oldUrl, newUrl) {
  const entry = dataStore.get(configId);
  if (!entry) throw new Error("config no encontrada");

  const items = await descargarYParsear(newUrl);

  if (oldUrl && oldUrl !== newUrl) delete entry.itemsPorUrl[oldUrl];
  entry.itemsPorUrl[newUrl] = items;

  if (oldUrl) {
    const idx = entry.m3uUrls.indexOf(oldUrl);
    if (idx !== -1) entry.m3uUrls[idx] = newUrl;
    else if (!entry.m3uUrls.includes(newUrl)) entry.m3uUrls.push(newUrl);
    if (oldUrl !== newUrl) delete entry.lastUpdated[oldUrl];
  } else if (!entry.m3uUrls.includes(newUrl)) {
    entry.m3uUrls.push(newUrl);
  }
  entry.lastUpdated[newUrl] = Date.now();

  const { movies, series, channels } = reagrupar(entry.itemsPorUrl);
  entry.movies = movies;
  entry.series = series;
  entry.channels = channels;
  entry.movieImdbIndex = {};
  entry.seriesImdbIndex = {};
  aplicarOverrides(entry);

  const rawConfig = configStore.get(configId);
  if (rawConfig) {
    rawConfig.m3uUrls = entry.m3uUrls;
    persistConfigStore();
  }

  prefetchTMDB(entry).catch(err =>
    console.error(`❌ [${configId}] Error en pre-carga TMDB tras actualizar lista:`, err)
  );

  return estadisticasDe(entry);
}

function quitarLista(configId, url) {
  const entry = dataStore.get(configId);
  if (!entry) throw new Error("config no encontrada");
  if (entry.m3uUrls.length <= 1) throw new Error("no se puede quitar la unica lista de la configuracion");

  delete entry.itemsPorUrl[url];
  entry.m3uUrls = entry.m3uUrls.filter(u => u !== url);
  delete entry.lastUpdated[url];

  const { movies, series, channels } = reagrupar(entry.itemsPorUrl);
  entry.movies = movies;
  entry.series = series;
  entry.channels = channels;
  entry.movieImdbIndex = {};
  entry.seriesImdbIndex = {};
  aplicarOverrides(entry);

  const rawConfig = configStore.get(configId);
  if (rawConfig) {
    rawConfig.m3uUrls = entry.m3uUrls;
    persistConfigStore();
  }

  return estadisticasDe(entry);
}

function estadisticasDeUrl(entry, url) {
  const items = entry.itemsPorUrl[url] || [];
  let movies = 0, series = 0, channels = 0, sinClasificar = 0;
  for (const item of items) {
    const { type } = classifyItem(item);
    if (type === "movie") movies++;
    else if (type === "series") series++;
    else if (type === "channel") channels++;
    else sinClasificar++;
  }
  return {
    url,
    total: items.length,
    movies, series, channels, sinClasificar,
    lastUpdated: entry.lastUpdated[url] || null
  };
}

function estadisticasDe(entry) {
  return {
    totales: {
      movies:   entry.movies.length,
      series:   Object.keys(entry.series).length,
      channels: entry.channels.length
    },
    porLista: entry.m3uUrls.map(url => estadisticasDeUrl(entry, url))
  };
}

const LOGO_SVG = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 256 256'>
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
</svg>`;

const LOGO = `data:image/svg+xml;base64,${Buffer.from(LOGO_SVG).toString("base64")}`;

const STREMIO_SIGNATURE =
  "eyJhbGciOiJkaXIiLCJlbmMiOiJBMTI4Q0JDLUhTMjU2In0..drO4si40GNH5_7aW8jgB9g.-1ysZnzhUaDVuZwvH2qcKs-pGPZ5D1ikiZQG1OfrWSNLrdVAU4wiuI1zXj2LtWNyn-ckw9K3be7ufwYrfXra0ty2W72J5wibK6spyF0n20oc925LpgsA2yhZvfYpGWeh.1RFI7MSVY2fm6IKI7dOqyw";

app.get("/", (req, res) => res.redirect("/configure"));

app.get("/configure", (req, res) => {
  res.sendFile(path.join(__dirname, "configure.html"));
});
app.get("/:configId/configure", (req, res) => {
  res.sendFile(path.join(__dirname, "configure.html"));
});

app.post("/api/config", async (req, res) => {
  const { m3uUrls, tmdbApiKey, githubToken, githubRepo, showChannels } = req.body;
  if (!Array.isArray(m3uUrls) || !m3uUrls.length) {
    return res.status(400).json({ error: "m3uUrls required" });
  }
  const config = { m3uUrls };
  if (tmdbApiKey) config.tmdbApiKey = tmdbApiKey;
  if (githubToken) config.githubToken = githubToken;
  if (githubRepo) config.githubRepo = githubRepo;
  if (showChannels) config.showChannels = true;
  const id = saveConfig(config);
  if (!dataStore.has(id)) {
    initData(config, id).catch(err =>
      console.error(`❌ [${id}] Error al cargar datos:`, err)
    );
  }
  res.json({ id });
});


app.get("/api/config/:configId", (req, res) => {
  const { configId } = req.params;
  const rawConfig = getConfig(configId);
  const entry = dataStore.get(configId);
  if (!rawConfig || !entry) return res.status(404).json({ error: "config not found" });
  res.json({
    configId,
    m3uUrls:      entry.m3uUrls,
    showChannels: !!entry.showChannels,
    hasTmdbKey:   !!entry.apiKey,
    hasGithub:    !!(entry.githubToken && entry.githubRepo),
    ready:        entry.ready,
    stats:        estadisticasDe(entry)
  });
});

app.put("/api/config/:configId/settings", (req, res) => {
  const { configId } = req.params;
  const entry = dataStore.get(configId);
  const rawConfig = getConfig(configId);
  if (!entry || !rawConfig) return res.status(404).json({ error: "config not found" });
  if (typeof req.body.showChannels === "boolean") {
    entry.showChannels = req.body.showChannels;
    rawConfig.showChannels = req.body.showChannels;
    persistConfigStore();
  }
  res.json({ ok: true });
});

app.put("/api/config/:configId/list", async (req, res) => {
  const { configId } = req.params;
  const { oldUrl, newUrl } = req.body;
  if (!newUrl || typeof newUrl !== "string") {
    return res.status(400).json({ error: "falta newUrl" });
  }
  try {
    const stats = await actualizarUnaLista(configId, oldUrl || null, newUrl.trim());
    res.json({ ok: true, stats });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.delete("/api/config/:configId/list", (req, res) => {
  const { configId } = req.params;
  const { url } = req.body;
  if (!url) return res.status(400).json({ error: "falta url" });
  try {
    const stats = quitarLista(configId, url);
    res.json({ ok: true, stats });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.get("/api/config/:configId/tmdb-search", async (req, res) => {
  const { configId } = req.params;
  const entry = dataStore.get(configId);
  if (!entry) return res.status(404).json({ error: "config not found" });
  if (!entry.apiKey) return res.status(400).json({ error: "no tmdb key configured for this config" });

  const q    = (req.query.q || "").toString().trim();
  const tipo = req.query.tipo === "series" ? "series" : "movie";
  if (!q) return res.status(400).json({ error: "missing q" });

  const endpoint = tipo === "series" ? "tv" : "movie";
  try {
    const searchRes = await axios.get(`https://api.themoviedb.org/3/search/${endpoint}`, {
      params: { api_key: entry.apiKey, query: q, language: "es-MX" },
      timeout: 10000
    });
    const top = (searchRes.data.results || []).slice(0, 8);
    const conImdb = await Promise.all(top.map(async r => {
      try {
        const detRes = await axios.get(`https://api.themoviedb.org/3/${endpoint}/${r.id}/external_ids`, {
          params: { api_key: entry.apiKey },
          timeout: 10000
        });
        const fecha = tipo === "series" ? r.first_air_date : r.release_date;
        return {
          tmdbId: r.id,
          title:  tipo === "series" ? r.name : r.title,
          year:   fecha ? fecha.slice(0, 4) : null,
          poster: r.poster_path ? `https://image.tmdb.org/t/p/w200${r.poster_path}` : null,
          imdbId: detRes.data.imdb_id || null
        };
      } catch {
        return null;
      }
    }));
    res.json({ resultados: conImdb.filter(r => r && r.imdbId) });
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
});

app.post("/api/config/:configId/override", (req, res) => {
  const { configId } = req.params;
  const entry = dataStore.get(configId);
  if (!entry) return res.status(404).json({ error: "config not found" });

  const { tipo, titulo, imdbId } = req.body;
  if (!["movie", "series"].includes(tipo)) {
    return res.status(400).json({ error: "tipo debe ser movie o series" });
  }
  if (!titulo || typeof titulo !== "string") {
    return res.status(400).json({ error: "falta titulo" });
  }
  if (!imdbId || !/^tt\d+$/.test(imdbId)) {
    return res.status(400).json({ error: "imdbId invalido (formato esperado: tt1234567)" });
  }

  const existeEnCatalogo = tipo === "movie"
    ? entry.movies.some(m => normalize(m.title) === normalize(titulo))
    : Object.values(entry.series).some(s => normalize(s.title) === normalize(titulo));
  if (!existeEnCatalogo) {
    return res.status(404).json({ error: "no se encontro ese titulo exacto en el catalogo actual" });
  }

  guardarOverride(configId, tipo, titulo, imdbId);
  aplicarOverrides(entry);
  res.json({ ok: true });
});

app.get("/manifest.json", (req, res) => {
  const baseUrl = `${req.protocol}://${req.get("host")}`;
  res.json({
    id:          "com.m3uiptv.public",
    version:     "1.1.4",
    name:        "M3U IPTV",
    description: "Stream your personal M3U playlist or Xtream Codes IPTV in Stremio. Auto-resolves IMDb IDs via TMDB. By Esmequiinn (reddit user Thin-Soil-4159)",
    logo:        "https://raw.githubusercontent.com/Esmequiinn/addon-m3u_friendly/main/logo.svg",
    resources:   ["catalog", "stream", "meta"],
    types:       ["movie", "series"],
    catalogs: [
      { type: "movie",  id: "m3u_movies", name: "My Movies",  extra: [{ name: "search", isRequired: false }] },
      { type: "series", id: "m3u_series", name: "My Series",  extra: [{ name: "search", isRequired: false }] }
    ],
    behaviorHints: {
      configurable:          true,
      configurationRequired: true,
      configureUrl:          `${baseUrl}/configure`
    },
    stremioAddonsConfig: {
      issuer:    "https://stremio-addons.net",
      signature: STREMIO_SIGNATURE
    }
  });
});

app.get("/:configId/manifest.json", (req, res) => {
  const config = getConfig(req.params.configId);
  if (!config) return res.status(404).json({ error: "Config not found. Please reconfigure the addon." });
  const baseUrl = `${req.protocol}://${req.get("host")}`;

  const catalogs = [
    {
      type: "movie", id: "m3u_movies", name: "Mis Películas",
      extra: [{ name: "search", isRequired: false }, { name: "skip", isRequired: false }]
    },
    {
      type: "series", id: "m3u_series", name: "Mis Series",
      extra: [{ name: "search", isRequired: false }, { name: "skip", isRequired: false }]
    }
  ];
  const types = ["movie", "series"];

  if (config.showChannels) {
    types.push("tv");
    catalogs.push({
      type: "tv", id: "m3u_channels", name: "Mis Canales",
      extra: [{ name: "search", isRequired: false }, { name: "skip", isRequired: false }]
    });
  }

  res.json({
    id:          "com.m3uiptv.public",
    version:     "1.1.4",
    name:        "M3U IPTV",
    description: "Reproduce tu lista M3U personal en Stremio con IDs IMDb automáticos. By Esmequiinn (reddit user Thin-Soil-4159)",
    logo:        LOGO,
    resources:   ["catalog", "stream", "meta"],
    types,
    catalogs,
    behaviorHints: { configurable: true, configureUrl: `${baseUrl}/${req.params.configId}/configure` },
    stremioAddonsConfig: {
      issuer:    "https://stremio-addons.net",
      signature: STREMIO_SIGNATURE
    }
  });
});

app.get("/:configId/catalog/:type/:id.json",        handleCatalog);
app.get("/:configId/catalog/:type/:id/:extra.json", handleCatalog);

function handleCatalog(req, res) {
  try {
    const entry = dataStore.get(req.params.configId);
    if (!entry || !entry.ready) return res.json({ metas: [] });
    const { type, id } = req.params;
    const extra = req.params.extra
      ? Object.fromEntries(new URLSearchParams(req.params.extra))
      : {};
    const search = extra.search ? normalize(extra.search) : null;
    const skip   = parseInt(extra.skip || "0", 10);
    const PAGE   = 100;
    if (type === "movie" && id === "m3u_movies") {
      let results = entry.movies;
      if (search) results = results.filter(m => normalize(m.title).includes(search));
      return res.json({
        metas: results.slice(skip, skip + PAGE).map(m => ({
          id: m.id, type: "movie", name: m.title, poster: m.poster
        }))
      });
    }
    if (type === "series" && id === "m3u_series") {
      let results = Object.values(entry.series);
      if (search) results = results.filter(s => normalize(s.title).includes(search));
      return res.json({
        metas: results.slice(skip, skip + PAGE).map(s => ({
          id: s.id, type: "series", name: s.title, poster: s.poster
        }))
      });
    }
    if (type === "tv" && id === "m3u_channels") {
      let results = entry.channels;
      if (search) results = results.filter(c => normalize(c.title).includes(search));
      return res.json({
        metas: results.slice(skip, skip + PAGE).map(c => ({
          id: c.id, type: "tv", name: c.title, poster: c.poster
        }))
      });
    }
    res.json({ metas: [] });
  } catch (err) {
    console.error("❌ Catalog error:", err);
    res.json({ metas: [] });
  }
}

app.get("/:configId/meta/:type/:id.json", async (req, res) => {
  try {
    const entry = dataStore.get(req.params.configId);
    if (!entry || !entry.ready) return res.json({ meta: null });
    const { type, id } = req.params;
    const { movies, series, tmdbCache, movieImdbIndex, seriesImdbIndex, apiKey } = entry;
    if (type === "movie") {
      const slugKey = movieImdbIndex[id] || id;
      let movie = movies.find(m => m.id === id || m.id === slugKey)
        || movies.find(m => normalize(m.title) === normalize(id));
      if (!movie) return res.json({ meta: null });
      if (!movie.id.startsWith("tt")) {
        const imdb = await searchTMDB(movie.title, "movie", tmdbCache, apiKey);
        if (imdb) { movieImdbIndex[imdb] = movie.id; movie.id = imdb; }
      }
      return res.json({
        meta: { id: movie.id, type: "movie", name: movie.title, poster: movie.poster }
      });
    }
    if (type === "series") {
      const slugKey = seriesImdbIndex[id] || id;
      let show = series[slugKey] || series[id]
        || Object.values(series).find(s => normalize(s.title) === normalize(id));
      if (!show) return res.json({ meta: null });
      if (!show.id.startsWith("tt")) {
        const imdb = await searchTMDB(show.title, "series", tmdbCache, apiKey);
        if (imdb) { seriesImdbIndex[imdb] = show.id; show.id = imdb; }
      }
      return res.json({
        meta: {
          id: show.id, type: "series", name: show.title, poster: show.poster,
          videos: show.episodes.map(ep => ({
            id:     `${show.id}:${ep.season}:${ep.episode}`,
            title:  ep.title,
            season: ep.season,
            number: ep.episode
          }))
        }
      });
    }
    if (type === "tv") {
      const channel = entry.channels.find(c => c.id === id)
        || entry.channels.find(c => normalize(c.title) === normalize(id));
      if (!channel) return res.json({ meta: null });
      return res.json({
        meta: { id: channel.id, type: "tv", name: channel.title, poster: channel.poster }
      });
    }
    res.json({ meta: null });
  } catch (err) {
    console.error("❌ Meta error:", err);
    res.json({ meta: null });
  }
});

app.get("/:configId/stream/:type/:id.json", async (req, res) => {
  try {
    const entry = dataStore.get(req.params.configId);
    if (!entry || !entry.ready) return res.json({ streams: [] });
    const { type, id } = req.params;
    const { movies, series, movieImdbIndex, seriesImdbIndex } = entry;
    if (type === "movie") {
      const slugKey = movieImdbIndex[id] || id;
      const movie = movies.find(m => m.id === id || m.id === slugKey)
        || movies.find(m => normalize(m.title) === normalize(id));
      if (!movie) return res.json({ streams: [] });
      return res.json({
        streams: movie.streams.map((s, i) => ({
          url: s.url, name: "M3U", title: `Stream ${i + 1}`
        }))
      });
    }
    if (type === "series") {
      const parts   = id.split(":");
      const rawId   = parts[0];
      const season  = parseInt(parts[1], 10);
      const episode = parseInt(parts[2], 10);
      const slugKey = seriesImdbIndex[rawId] || rawId;
      const show = series[slugKey] || series[rawId]
        || Object.values(series).find(s =>
            normalize(s.title) === normalize(rawId) || s.id === rawId
          );
      if (!show) return res.json({ streams: [] });
      const eps = show.episodes.filter(
        e => e.season === season && e.episode === episode
      );
      return res.json({
        streams: eps.map((ep, i) => ({
          url: ep.url, name: "M3U", title: `Stream ${i + 1}`
        }))
      });
    }
    if (type === "tv") {
      const channel = entry.channels.find(c => c.id === id)
        || entry.channels.find(c => normalize(c.title) === normalize(id));
      if (!channel) return res.json({ streams: [] });
      return res.json({
        streams: channel.streams.map((s, i) => ({
          url: s.url, name: "M3U", title: `Canal ${i + 1}`
        }))
      });
    }
    res.json({ streams: [] });
  } catch (err) {
    console.error("❌ Stream error:", err);
    res.json({ streams: [] });
  }
});


function startKeepAlive(baseUrl) {
  setInterval(async () => {
    try {
      await fetch(`${baseUrl}/manifest.json`);
      console.log(`💓 Keep-alive OK`);
    } catch (err) {
      console.error(`❌ Keep-alive error:`, err.message);
    }
  }, 14 * 60 * 1000);
}

app.listen(PORT, async () => {
  console.log(`🚀 M3U IPTV corriendo en http://localhost:${PORT}`);

  const envUrls = process.env.M3U_URLS
    ? process.env.M3U_URLS.split(",").map(u => u.trim()).filter(Boolean)
    : process.env.M3U_URL
      ? [process.env.M3U_URL.trim()]
      : [];

  if (envUrls.length) {
    const config = { m3uUrls: envUrls };
    if (process.env.TMDB_API_KEY) config.tmdbApiKey = process.env.TMDB_API_KEY;
    if (process.env.GITHUB_TOKEN) config.githubToken = process.env.GITHUB_TOKEN;
    if (process.env.GITHUB_REPO)  config.githubRepo  = process.env.GITHUB_REPO;
    if (process.env.SHOW_CHANNELS === "true") config.showChannels = true;
    const id = saveConfig(config);
    initData(config, id).catch(err => console.error(`❌ [${id}] Error al cargar datos:`, err));
  }

  if (configStore.size > 0) {
    console.log(`🔁 Restaurando ${configStore.size} configuracion(es) guardada(s)...`);
    for (const [id, cfg] of configStore.entries()) {
      if (dataStore.has(id)) continue;
      initData(cfg, id).catch(err => console.error(`❌ [${id}] Error al restaurar:`, err));
    }
  }

  if (!envUrls.length && configStore.size === 0) {
    console.log("⚠️  Sin listas configuradas — configura desde /configure");
  }

  const publicUrl = process.env.RENDER_EXTERNAL_URL;
  if (publicUrl) {
    console.log(`💓 Keep-alive iniciado: ${publicUrl}`);
    startKeepAlive(publicUrl);
  }
});
