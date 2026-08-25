
const SEASON_EP_RE = new RegExp(
  "(?:[Ss](\\d{1,2})\\s*[Ee](\\d{1,3}))" +
  "|(?:[Tt](\\d{1,2})\\s*[Ee](\\d{1,3}))" +
  "|(?:(?<!\\d)(\\d{1,2})x(\\d{1,3})(?!\\d))" +
  "|(?:temporada\\s*(\\d{1,2})\\s*(?:episodio|ep|cap[ií]tulo|cap)\\.?\\s*(\\d{1,3}))",
  "i"
);

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

const CHANNEL_GROUP_KEYWORDS = [
  "tv en vivo", "live tv", "canales en vivo",
  "canales", "channels", "live channels",
  "noticias", "news", "deportes en vivo",
  "sports live", "radio", "musica en vivo",
  "adult", "xxx", "18+", "24h", "24/7"
];

const LIVE_URL_RE = /\/(live|stream|iptv|livetv|channel)\//i;


function slugify(str) {
  return str
    .toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_]/g, "")
    .slice(0, 60);
}


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


function extractAttr(str, attr) {
  const re = new RegExp(`${attr}="([^"]*)"`, "i");
  const m = str.match(re);
  return m ? m[1].trim() : null;
}

function cleanTitle(str = "") {
  return str
    .replace(/tvg-[a-z-]+="[^"]*"/gi, "")
    .replace(/group-title="[^"]*"/gi, "")
    .replace(/[a-z-]+="[^"]*"/gi, "")
    .replace(/\s*[·|]?\s*📦\s*[\d.,]+\s*(?:B|KB|MB|GB|TB)\b/gi, "")
    .replace(/\b(19|20)\d{2}\b/g, m => `__YEAR_${m}__`)
    .replace(/(?<=[a-zA-Z])\.(?=[a-zA-Z0-9(\[])/g, " ")
    .replace(/1080p|720p|2160p|4k|hdr|webrip|bluray|x264|x265|hevc|avc/gi, "")
    .replace(/\b(bdrip|brrip|hdrip|dvdrip|hdtv|remux|extended|imax)\b/gi, "")
    .replace(/\b(cam|hdcam|hqcam|camrip|hdts|scr|screener|telesync|telecine)\b/gi, "")
    .replace(/\bversion\s*no\s*definitiva\b|\bno\s*definitiva\b|\bcalidad\b/gi, "")
    .replace(/\bac-?3(?:\s*\d(?:\.\d)?)?\b/gi, "")
    .replace(/\b\d{2,3}\s*fps\b/gi, "")
    .replace(/[\d.,]+\s*mbps\b/gi, "")
    .replace(/[\d.,]+\s*gb\b/gi, "")
    .replace(/latino|castellano|cast\b|espa[ñn]ol|japon[eé]s|ingl[eé]s|multi\b|dual|subtitulado|sub\b/gi, "")
    .replace(/\b(lat|esp|spa|eng|jpn|jap)\b/gi, "")
    .replace(/\b(animaci[oó]n|acci[oó]n|comedia|terror|drama|suspenso|aventura|fantas[ií]a|ciencia\s*ficci[oó]n|romance|documental|anime|musical|b[eé]lica|crimen|misterio|thriller|western|biograf[ií]a|familiar|infantil)\b/gi, "")
    .replace(/__YEAR_(\d{4})__/g, "$1")
    .replace(/\[[\s,]*\]/g, "")
    .replace(/\([\s,]*\)/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanTitleForTMDB(str = "") {
  return str
    .replace(/\s*[·|]?\s*📦\s*[\d.,]+\s*(?:B|KB|MB|GB|TB)\b/gi, "")
    .replace(/\([^)]*\)/g, "")
    .replace(/\[[^\]]*\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function hasYear(str) {
  return /\b(19[5-9]\d|20[0-2]\d)\b/.test(str);
}

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

function classifyItem(item) {
  const allText = `${item.title} ${item.tvgName} ${item.group}`.toLowerCase();
  const groupLow = item.group.toLowerCase();

  const epMatch =
    SEASON_EP_RE.exec(item.title) ||
    SEASON_EP_RE.exec(item.tvgName);

  if (epMatch) return { type: "series", match: epMatch };

  if (item.tvgId && item.tvgId.startsWith("tt")) {
    return { type: "movie" };
  }

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

function groupContent(items) {
  const moviesMap = {};
  const series = {};
  const channelsMap = {};

  let countUnknown = 0;
  let countSerieSinMatch = 0;

  for (const item of items) {
    const { type, match: seMatch } = classifyItem(item);

    if (type === "channel") {
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


function pad(n) {
  return String(n).padStart(2, "0");
}

module.exports = { parseM3U, groupContent, cleanTitleForTMDB, classifyItem };
