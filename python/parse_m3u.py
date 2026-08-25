import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional



SEASON_EP_RE = re.compile(
    r"(?:[Ss](\d{1,2})\s*[Ee](\d{1,3}))"
    r"|(?:[Tt](\d{1,2})\s*[Ee](\d{1,3}))"
    r"|(?:(?<!\d)(\d{1,2})x(\d{1,3})(?!\d))"
    r"|(?:temporada\s*(\d{1,2})\s*(?:episodio|ep|cap[íi]tulo|cap)\.?\s*(\d{1,3}))",
    re.IGNORECASE,
)

SERIES_GROUP_KEYWORDS = [
    "serie", "series", "show", "shows",
    "temporada", "season", "novela", "anime",
    "dorama", "miniserie",
]

MOVIE_GROUP_KEYWORDS = [
    "peli", "pelicula", "película", "peliculas", "películas",
    "movie", "movies", "film", "films", "cine",
    "estreno", "estrenos", "4k", "hd", "bluray",
    "latino", "castellano", "español", "dubbed",
    "mis peli", "mis movie", "coleccion", "colección",
    "accion", "acción", "comedia", "drama", "terror", "thriller",
    "animacion", "animación", "documental", "western", "sci-fi",
]

CHANNEL_GROUP_KEYWORDS = [
    "tv en vivo", "live tv", "canales en vivo",
    "canales", "channels", "live channels",
    "noticias", "news", "deportes en vivo",
    "sports live", "radio", "musica en vivo",
    "adult", "xxx", "18+", "24h", "24/7",
    "futbol", "fútbol", "soccer", "sports", "deportes",
]

DISCARD_TITLE_KEYWORDS = [
    "mundial", "world cup", "copa del mundo", "fifa",
    "futbol", "fútbol", "soccer", "football",
    "nfl", "nba", "mlb", "nhl", "ufc", "mma",
    "liga mx", "champions", "premier league", "laliga", "la liga",
    "serie a", "bundesliga", "ligue 1",
    "deportes", "sports", "sport",
    "ven vip", "venvip",
    "ppv", "pay per view",
    "zapat", "zapat",
]

DISCARD_GROUP_KEYWORDS = [
    "futbol", "fútbol", "soccer", "football", "deportes", "sports",
    "mundial", "world cup", "fifa", "ppv",
    "eventos", "events", "lucha libre", "boxing", "boxeo",
    "nfl", "nba", "mlb", "nhl", "ufc", "mma",
]

LIVE_URL_RE = re.compile(r"/(live|stream|iptv|livetv|channel)/", re.IGNORECASE)


@dataclass
class M3UItem:
    title: str
    logo: Optional[str]
    group: str
    tvg_name: str
    tvg_id: Optional[str]
    tvg_language: Optional[str]
    url: Optional[str] = None


@dataclass
class Episode:
    season: int
    episode: int
    title: str
    url: str


@dataclass
class Series:
    id: str
    title: str
    poster: Optional[str]
    genres: list
    episodes: list = field(default_factory=list)


@dataclass
class Movie:
    id: str
    title: str
    poster: Optional[str]
    genres: list
    streams: list = field(default_factory=list)


@dataclass
class Channel:
    id: str
    title: str
    poster: Optional[str]
    genres: list
    streams: list = field(default_factory=list)


def slugify(s: str) -> str:


    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9_]", "", s.replace(" ", "_"))[:60]


def extract_attr(line: str, attr: str) -> Optional[str]:
    m = re.search(rf'{attr}="([^"]*)"', line, re.IGNORECASE)
    return m.group(1).strip() if m else None


def clean_title(s: str = "") -> str:
    s = re.sub(r'tvg-[a-z-]+="[^"]*"', "", s, flags=re.IGNORECASE)
    s = re.sub(r'group-title="[^"]*"', "", s, flags=re.IGNORECASE)
    s = re.sub(r'[a-z-]+="[^"]*"', "", s, flags=re.IGNORECASE)

    s = re.sub(r"\s*[·|]?\s*📦\s*[\d.,]+\s*(?:B|KB|MB|GB|TB)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(19|20)\d{2}\b", lambda m: f"__YEAR_{m.group()}__", s)



    s = re.sub(r"(?<=[a-zA-Z])\.(?=[a-zA-Z0-9(\[])", " ", s)
    s = re.sub(r"1080p|720p|2160p|4k|hdr|webrip|bluray|x264|x265|hevc|avc", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(bdrip|brrip|hdrip|dvdrip|hdtv|remux|extended|imax)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(cam|hdcam|hqcam|camrip|hdts|scr|screener|telesync|telecine)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bversion\s*no\s*definitiva\b|\bno\s*definitiva\b|\bcalidad\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bac-?3(?:\s*\d(?:\.\d)?)?\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\d{2,3}\s*fps\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[\d.,]+\s*mbps\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[\d.,]+\s*gb\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"latino|castellano|cast\b|espa[ñn]ol|japon[eé]s|ingl[eé]s|multi\b|dual|subtitulado|sub\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(lat|esp|spa|eng|jpn|jap)\b", "", s, flags=re.IGNORECASE)


    s = re.sub(
        r"\b(animaci[oó]n|acci[oó]n|comedia|terror|drama|suspenso|aventura|fantas[ií]a|"
        r"ciencia\s*ficci[oó]n|romance|documental|anime|musical|b[eé]lica|crimen|misterio|"
        r"thriller|western|biograf[ií]a|familiar|infantil)\b",
        "", s, flags=re.IGNORECASE,
    )
    s = re.sub(r"__YEAR_(\d{4})__", r"\1", s)
    s = re.sub(r"\[[\s,]*\]", "", s)
    s = re.sub(r"\([\s,]*\)", "", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_title_for_tmdb(s: str = "") -> str:
    s = re.sub(r"\s*[·|]?\s*📦\s*[\d.,]+\s*(?:B|KB|MB|GB|TB)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\[[^\]]*\]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def has_year(s: str) -> bool:
    return bool(re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", s))



def es_url_de_canal_en_vivo(url: Optional[str]) -> bool:
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        partes = [p for p in urlparse(url).path.split("/") if p]
        if partes and re.match(r"^(movie|series)$", partes[0], re.IGNORECASE):
            return False
        return len(partes) == 3 and partes[2].isdigit()
    except Exception:
        return False


def pad(n: int) -> str:
    return str(n).zfill(2)



def parse_extinf(line: str) -> M3UItem:
    title_match = re.search(r",(.+)$", line)
    title = title_match.group(1).strip() if title_match else "Sin título"
    return M3UItem(
        title=title,
        logo=extract_attr(line, "tvg-logo") or extract_attr(line, "tvg-logo-url"),
        group=extract_attr(line, "group-title") or "",
        tvg_name=extract_attr(line, "tvg-name") or title,
        tvg_id=extract_attr(line, "tvg-id"),
        tvg_language=extract_attr(line, "tvg-language"),
    )


def parse_m3u(raw: str) -> list[M3UItem]:
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    items: list[M3UItem] = []
    current: Optional[M3UItem] = None

    for line in lines:
        if line.startswith("#EXTINF"):
            current = parse_extinf(line)
        elif line.startswith("#"):
            continue
        elif current:
            current.url = line
            items.append(current)
            current = None

    return items


def contiene_palabra(texto, palabras):
    for palabra in palabras:
        if re.search(rf"\b{re.escape(palabra)}\b", texto):
            return True
    return False


def classify_item(item: M3UItem):
    group_low = item.group.lower()
    title_low = (item.title + " " + item.tvg_name).lower()

    if contiene_palabra(title_low, DISCARD_TITLE_KEYWORDS):
        return "channel", None
    if contiene_palabra(group_low, DISCARD_GROUP_KEYWORDS):
        return "channel", None

    ep_match = SEASON_EP_RE.search(item.title) or SEASON_EP_RE.search(item.tvg_name)
    if ep_match:
        return "series", ep_match

    if item.tvg_id and item.tvg_id.startswith("tt"):
        return "movie", None

    if es_url_de_canal_en_vivo(item.url):
        return "channel", None

    if contiene_palabra(group_low, CHANNEL_GROUP_KEYWORDS):
        return "channel", None

    if contiene_palabra(group_low, SERIES_GROUP_KEYWORDS):
        return "series", None

    if contiene_palabra(group_low, MOVIE_GROUP_KEYWORDS):
        return "movie", None

    all_text = f"{item.title} {item.tvg_name} {item.group}".lower()
    if has_year(all_text):
        return "movie", None

    if item.url and LIVE_URL_RE.search(item.url):
        return "channel", None

    if item.url and re.search(r"\.(mp4|mkv|avi|mov|ts|m4v|wmv)(\?|$)", item.url, re.IGNORECASE):
        return "movie", None

    return "unknown", None



def group_content(items: list[M3UItem]) -> dict:
    movies_map: dict[str, Movie] = {}
    series_map: dict[str, Series] = {}
    channels_map: dict[str, Channel] = {}
    count_unknown = 0
    count_serie_sin_match = 0

    for item in items:
        content_type, se_match = classify_item(item)

        if content_type == "channel":



            channel_id = item.tvg_id or slugify(item.tvg_name or item.title)
            if channel_id not in channels_map:
                channels_map[channel_id] = Channel(
                    id=channel_id,
                    title=(item.tvg_name or item.title).strip(),
                    poster=item.logo,
                    genres=[item.group] if item.group else [],
                )
            channels_map[channel_id].streams.append({"url": item.url})
            continue

        if content_type == "unknown":
            count_unknown += 1
            continue



        if content_type == "series" and not se_match:
            count_serie_sin_match += 1
            continue

        if content_type == "series" and se_match:
            g = se_match.groups()
            season  = int(next(x for x in (g[0], g[2], g[4], g[6]) if x is not None))
            episode = int(next(x for x in (g[1], g[3], g[5], g[7]) if x is not None))

            raw_name = clean_title_for_tmdb(
                clean_title(
                    re.sub(SEASON_EP_RE, "", item.tvg_name or item.title)
                    .rstrip("-–_. ")
                    .strip()
                )
            )

            series_id = (
                item.tvg_id
                if item.tvg_id and item.tvg_id.startswith("tt")
                else slugify(raw_name)
            )

            if series_id not in series_map:
                series_map[series_id] = Series(
                    id=series_id,
                    title=raw_name,
                    poster=item.logo,
                    genres=[item.group] if item.group else [],
                )

            series_map[series_id].episodes.append(
                Episode(
                    season=season,
                    episode=episode,
                    title=f"S{pad(season)}E{pad(episode)}",
                    url=item.url,
                )
            )

        else:
            cleaned_title = clean_title(item.tvg_name or item.title)
            movie_id = (
                item.tvg_id
                if item.tvg_id and item.tvg_id.startswith("tt")
                else slugify(cleaned_title)
            )

            if movie_id not in movies_map:
                movies_map[movie_id] = Movie(
                    id=movie_id,
                    title=cleaned_title,
                    poster=item.logo,
                    genres=[item.group] if item.group else [],
                )

            movies_map[movie_id].streams.append({"url": item.url})

    print(
        f"📡 Canales agrupados (ocultos salvo que se activen): {len(channels_map)} "
        f"| ❓ Sin clasificar (descartados): {count_unknown} "
        f"| 📺 Series sin temporada/episodio detectable (descartados): {count_serie_sin_match}"
    )

    return {
        "movies": list(movies_map.values()),
        "series": series_map,
        "channels": list(channels_map.values()),
    }
