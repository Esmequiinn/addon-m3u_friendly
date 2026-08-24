/**
 * parse-m3u.js
 * Detecta películas y series, ignora canales IPTV
 *
 * Formatos de episodio soportados:
 *   S01E01 / S01 E01
 *   1x01 / 01x01
 *   T01E01 (español)
 *   Temporada 1 Episodio 1
 *   Capitulo 1 / Cap 1
 */

// ─────────────────────────────────────────────
// REGEX EPISODIOS
// Cubre todos los formatos comunes
// ─────────────────────────────────────────────
const SEASON_EP_RE = new RegExp(
  // S01E01 / S01 E01 -- el episodio admite hasta 3 dígitos (E001 a E999),
  // series largas (Dragon Ball Super, novelas, etc) pasan del episodio 99
  // y con solo 2 dígitos el numero se cortaba mal (E100 se leia como "10"
  // y el "0" que sobraba quedaba pegado al titulo)
  "(?:[Ss](\\d{1,2})\\s*[Ee](\\d{1,3}))" +
  // T01E01 (español)
  "|(?:[Tt](\\d{1,2})\\s*[Ee](\\d{1,3}))" +
  // 1x01 / 01x01 -- el (?<!\d) y (?!\d) evitan que una resolucion de video
  // tipo "1920x1080" se lea como si fuera un episodio "20x10"
  "|(?:(?<!\\d)(\\d{1,2})x(\\d{1,3})(?!\\d))" +
  // Temporada 1 Episodio 1
  "|(?:temporada\\s*(\\d{1,2})\\s*(?:episodio|ep|cap[ií]tulo|cap)\\.?\\s*(\\d{1,3}))",
  "i"
);

// ─────────────────────────────────────────────
// KEYWORDS grupos de series/pelis
// ─────────────────────────────────────────────
const SERIES_GROUP_KEYWORDS = [
  "serie", "series", "show", "shows",
  "temporada", "season", "novela", "anime",
  "dorama", "miniserie"
];

const MOVIE_GROUP_KEYWORDS = [
  "peli", "pelicula", "película", "peliculas", "películas",
  "movie", "movies", "film", "films", "cine",
  "estreno", "estrenos", "4k", "hd", "bluray",
  "latino", "castellano", "español", "dubbed"
];

// ─────────────────────────────────────────────
// KEYWORDS grupos que son CLARAMENTE canales de TV
// Solo los muy específicos — no palabras que aparezcan en títulos
// ─────────────────────────────────────────────
const CHANNEL_GROUP_KEYWORDS = [
  "tv en vivo", "live tv", "canales en vivo",
  "canales", "channels", "live channels",
  "noticias", "news", "deportes en vivo",
  "sports live", "radio", "musica en vivo",
  "adult", "xxx", "18+", "24h", "24/7"
];

// Patrones de URL que indican stream en vivo (NO grabaciones)
const LIVE_URL_RE = /\/(live|stream|iptv|livetv|channel)\//i;

// ─────────────────────────────────────────────

function slugify(str) {
  return str
    .toLowerCase()
    // Sin esto las vocales acentuadas y la ñ se borraban enteras en vez de
    // convertirse a su letra sin tilde ("Teoría" quedaba "tera", no "teoria")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_]/g, "")
    .slice(0, 60);
}

// ─────────────────────────────────────────────

function parseM3U(raw) {
  const lines = raw
    .split(/\r?\n/)
    .map(l => l.trim())
    .filter(Boolean);

  const items = [];
  let current = null;

  for (const line of lines) {
    if (line.startsWith("#EXTINF")) {
      current = parseExtInf(line);
    } else if (line.startsWith("#")) {
      continue;
    } else if (current) {
      current.url = line;
      items.push(current);
      current = null;
    }
  }

  return items;
}

// ─────────────────────────────────────────────

function parseExtInf(line) {
  const titleMatch = line.match(/,(.+)$/);
  const title = titleMatch ? titleMatch[1].trim() : "Sin título";

  return {
    title,
    logo:        extractAttr(line, "tvg-logo") || extractAttr(line, "tvg-logo-url") || null,
    group:       extractAttr(line, "group-title") || "",
    tvgName:     extractAttr(line, "tvg-name") || title,
    tvgId:       extractAttr(line, "tvg-id") || null,
    tvgLanguage: extractAttr(line, "tvg-language") || null,
    url:         null
  };
}

// ─────────────────────────────────────────────

function extractAttr(str, attr) {
  const re = new RegExp(`${attr}="([^"]*)"`, "i");
  const m = str.match(re);
  return m ? m[1].trim() : null;
}

// ─────────────────────────────────────────────
// cleanTitle
// Elimina atributos M3U, calidad, idioma del texto del título
// ─────────────────────────────────────────────
function cleanTitle(str = "") {
  return str
    .replace(/tvg-[a-z-]+="[^"]*"/gi, "")
    .replace(/group-title="[^"]*"/gi, "")
    .replace(/[a-z-]+="[^"]*"/gi, "")
    // tamaño de archivo pegado al titulo, ej "· 📦 1.41 GB"
    .replace(/\s*[·|]?\s*📦\s*[\d.,]+\s*(?:B|KB|MB|GB|TB)\b/gi, "")
    .replace(/\b(19|20)\d{2}\b/g, m => `__YEAR_${m}__`) // preservar año temporalmente
    // puntos entre letras (muchas listas separan palabras con puntos en vez
    // de espacios, "Spider-Man.No.Way.Home") y entre letra y numero/parentesis
    // ("Chapter.1.(2014)") -- deja intactos los puntos entre dos numeros para
    // no romper un decimal como "5.3" de un bitrate
    .replace(/(?<=[a-zA-Z])\.(?=[a-zA-Z0-9(\[])/g, " ")
    .replace(/1080p|720p|2160p|4k|hdr|webrip|bluray|x264|x265|hevc|avc/gi, "")
    // fuente/formato de la copia
    .replace(/\b(bdrip|brrip|hdrip|dvdrip|hdtv|remux|extended|imax)\b/gi, "")
    .replace(/\b(cam|hdcam|hqcam|camrip|hdts|scr|screener|telesync|telecine)\b/gi, "")
    .replace(/\bversion\s*no\s*definitiva\b|\bno\s*definitiva\b|\bcalidad\b/gi, "")
    .replace(/\bac-?3(?:\s*\d(?:\.\d)?)?\b/gi, "")
    .replace(/\b\d{2,3}\s*fps\b/gi, "")
    .replace(/[\d.,]+\s*mbps\b/gi, "")
    .replace(/[\d.,]+\s*gb\b/gi, "")
    .replace(/latino|castellano|cast\b|espa[ñn]ol|japon[eé]s|ingl[eé]s|multi\b|dual|subtitulado|sub\b/gi, "")
    .replace(/\b(lat|esp|spa|eng|jpn|jap)\b/gi, "")
    // genero pegado al final del titulo por el proveedor ("Hoppers 2026
    // Animación") -- sin esto el titulo real nunca matcheaba exacto contra
    // TMDB/Cinemeta ("hoppers" vs "hoppers animacion")
    .replace(/\b(animaci[oó]n|acci[oó]n|comedia|terror|drama|suspenso|aventura|fantas[ií]a|ciencia\s*ficci[oó]n|romance|documental|anime|musical|b[eé]lica|crimen|misterio|thriller|western|biograf[ií]a|familiar|infantil)\b/gi, "")
    .replace(/__YEAR_(\d{4})__/g, "$1") // restaurar año
    .replace(/\[[\s,]*\]/g, "") // corchetes vacios que quedaron tras limpiar
    .replace(/\([\s,]*\)/g, "") // idem parentesis
    .replace(/\s+/g, " ")
    .trim();
}

// Limpia título para buscar en TMDB — elimina paréntesis/corchetes
// que son etiquetas del proveedor: (Trial Audio), (CAST.), [HD], etc.
function cleanTitleForTMDB(str = "") {
  return str
    .replace(/\s*[·|]?\s*📦\s*[\d.,]+\s*(?:B|KB|MB|GB|TB)\b/gi, "")
    .replace(/\([^)]*\)/g, "")
    .replace(/\[[^\]]*\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// ─────────────────────────────────────────────
// hasYear — detecta si el título contiene un año de producción
// Señal fuerte de que es película o serie, no canal
// ─────────────────────────────────────────────
function hasYear(str) {
  return /\b(19[5-9]\d|20[0-2]\d)\b/.test(str);
}

// Señal ESTRUCTURAL de Xtream, no depende de adivinar palabras del grupo
// (que varian sin limite entre proveedores e idiomas): los VOD siempre
// tienen "/movie/" o "/series/" en el path y un canal en vivo termina en
// el numero de stream pelado, sin extension. Probado contra una lista real
// de 60mil+ items -- categorias como "FOX SPORTS" o "HBO PREMIUM" no
// calzaban con ninguna palabra clave y se colaban sin esta señal.
function esUrlDeCanalEnVivo(url) {
  if (!url) return false;
  try {
    const partes = new URL(url).pathname.split("/").filter(Boolean);
    if (partes[0] && /^(movie|series)$/i.test(partes[0])) return false;
    return partes.length === 3 && /^\d+$/.test(partes[2]);
  } catch {
    return false;
  }
}

// ─────────────────────────────────────────────
// classifyItem — decide qué es cada item
// Retorna: "series" | "movie" | "channel" | "unknown"
//
// Orden de prioridad:
//  1. ¿Tiene patrón de episodio?         → series (certeza alta)
//  1b. ¿Ya trae tvg-id de IMDb?          → movie (certeza total)
//  1c. ¿URL con forma de canal Xtream?   → channel
//  2. ¿Grupo claramente de canal?        → channel
//  3. ¿Grupo claramente de serie?        → series
//  4. ¿Grupo claramente de película?     → movie
//  5. ¿Tiene año en el título?           → movie (probabilidad alta)
//  6. ¿URL de live stream?               → channel
//  7. Sin suficiente info                → unknown (se descarta)
// ─────────────────────────────────────────────
function classifyItem(item) {
  const allText = `${item.title} ${item.tvgName} ${item.group}`.toLowerCase();
  const groupLow = item.group.toLowerCase();

  const epMatch =
    SEASON_EP_RE.exec(item.title) ||
    SEASON_EP_RE.exec(item.tvgName);

  if (epMatch) return { type: "series", match: epMatch };

  // Ya viene con tvg-id de IMDb -- pelicula confirmada, no hace falta
  // adivinar por grupo/año/url. Sin esto una pelicula sin año detectable
  // en el nombre y sin group-title caia en la regla de URL de stream solo
  // por tener "/stream/" en la url de reproduccion, y se descartaba como
  // canal en vivo.
  if (item.tvgId && item.tvgId.startsWith("tt")) {
    return { type: "movie" };
  }

  // Se prueba antes que las listas de palabras clave porque es mas
  // confiable: no depende de en que idioma cada proveedor etiquete sus
  // categorias.
  if (esUrlDeCanalEnVivo(item.url)) {
    return { type: "channel" };
  }

  if (CHANNEL_GROUP_KEYWORDS.some(kw => groupLow.includes(kw))) {
    return { type: "channel" };
  }

  if (SERIES_GROUP_KEYWORDS.some(kw => groupLow.includes(kw))) {
    return { type: "series", match: null };
  }

  if (MOVIE_GROUP_KEYWORDS.some(kw => groupLow.includes(kw))) {
    return { type: "movie" };
  }

  if (hasYear(allText)) {
    return { type: "movie" };
  }

  if (item.url && LIVE_URL_RE.test(item.url)) {
    return { type: "channel" };
  }

  return { type: "unknown" };
}

// ─────────────────────────────────────────────
// groupContent
// ─────────────────────────────────────────────
function groupContent(items) {
  const moviesMap = {};
  const series = {};
  const channelsMap = {};

  let countUnknown = 0;
  let countSerieSinMatch = 0;

  for (const item of items) {
    const { type, match: seMatch } = classifyItem(item);

    if (type === "channel") {
      // Se agrupan igual que las peliculas (mismo canal, varias listas ->
      // un solo item con varias opciones de stream) -- pero por defecto
      // esto NO se muestra en ningun catalogo, se descarta en addon.js
      // salvo que el usuario active "mostrar canales de tv" al configurar.
      // Se sigue armando siempre igual (es barato, ya se esta recorriendo
      // el item) para que activar/desactivar la casilla no requiera
      // volver a bajar ni reprocesar las listas.
      const channelId = item.tvgId || slugify(item.tvgName || item.title);
      if (!channelsMap[channelId]) {
        channelsMap[channelId] = {
          id:     channelId,
          title:  (item.tvgName || item.title).trim(),
          poster: item.logo || null,
          genres: item.group ? [item.group] : [],
          streams: []
        };
      }
      channelsMap[channelId].streams.push({ url: item.url });
      continue;
    }
    if (type === "unknown") { countUnknown++; continue; }

    // Si algo se detecto como serie por el nombre del grupo pero no se le
    // pudo sacar temporada/episodio del titulo, no hay forma segura de
    // ubicarlo dentro de la serie -- antes esto caia por descarte a
    // PELICULAS, mezclando episodios sueltos con una pelicula que se
    // llame igual. Mejor descartar ese item puntual.
    if (type === "series" && !seMatch) {
      countSerieSinMatch++;
      continue;
    }

    if (type === "series" && seMatch) {
      const season  = parseInt(seMatch[1] || seMatch[3] || seMatch[5] || seMatch[7], 10);
      const episode = parseInt(seMatch[2] || seMatch[4] || seMatch[6] || seMatch[8], 10);

      if (isNaN(season) || isNaN(episode)) continue;

      const rawName = cleanTitleForTMDB(
        cleanTitle(
          (item.tvgName || item.title)
            .replace(SEASON_EP_RE, "")
            .replace(/[-–_.\s]+$/, "")
            .trim()
        )
      );

      const seriesId =
        item.tvgId && item.tvgId.startsWith("tt")
          ? item.tvgId
          : slugify(rawName);

      if (!series[seriesId]) {
        series[seriesId] = {
          id:     seriesId,
          title:  rawName,
          poster: item.logo || null,
          genres: item.group ? [item.group] : [],
          episodes: []
        };
      }

      series[seriesId].episodes.push({
        season,
        episode,
        title: `S${pad(season)}E${pad(episode)}`,
        url:   item.url
      });

    } else {
      const cleanedTitle = cleanTitle(item.tvgName || item.title);

      const movieId =
        item.tvgId && item.tvgId.startsWith("tt")
          ? item.tvgId
          : slugify(cleanedTitle);

      if (!moviesMap[movieId]) {
        moviesMap[movieId] = {
          id:     movieId,
          title:  cleanedTitle,
          poster: item.logo || null,
          genres: item.group ? [item.group] : [],
          streams: []
        };
      }

      moviesMap[movieId].streams.push({ url: item.url });
    }
  }

  const countChannel = Object.keys(channelsMap).length;
  console.log(`📡 Canales agrupados (ocultos salvo que se activen): ${countChannel} | ❓ Sin clasificar (descartados): ${countUnknown} | 📺 Series sin temporada/episodio detectable (descartados): ${countSerieSinMatch}`);

  return {
    movies: Object.values(moviesMap),
    series,
    channels: Object.values(channelsMap)
  };
}

// ─────────────────────────────────────────────

function pad(n) {
  return String(n).padStart(2, "0");
}

module.exports = { parseM3U, groupContent, cleanTitleForTMDB };
