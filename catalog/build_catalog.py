#!/usr/bin/env python3
"""
TakeABreak book catalog (weekly, GitHub Actions).

Reads Project Gutenberg's public-domain catalog data (the RDF feed, which Gutenberg offers for this kind of use),
keeps English books that are free to read in India, and writes small tab-separated files the app downloads:

  books/v1/index.tsv        every book, most read first:  id  title  author  words  tags
  books/v1/shelves.tsv      the Library's shelves:        key  title  subtitle  ids (comma separated)
  books/v1/about/<n>.tsv    short descriptions, 100 ids per file (n = id // 100):  id  text
  books/v1/meta.txt         build date and counts

India: copyright lasts 60 years after the author's death (counted from the next 1 January), so every author and
translator must have died in YEAR - 61 or earlier. Books marked "Copyrighted" by Gutenberg are left out.
"""
import collections, datetime, io, os, re, sys, tarfile, time, urllib.request
import xml.etree.ElementTree as ET

RDF_URL = "https://www.gutenberg.org/cache/epub/feeds/rdf-files.tar.bz2"
MIRROR = "https://gutenberg.pglaf.org/cache/epub"
UA = "TakeABreak-catalog/1.0 (+https://trexnugget.github.io/takeabreak/)"

NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dcterms": "http://purl.org/dc/terms/",
    "pgterms": "http://www.gutenberg.org/2009/pgterms/",
    "marcrel": "http://id.loc.gov/vocabulary/relators/",
    "dcam": "http://purl.org/dc/dcam/",
}
RDF_ABOUT = "{%s}about" % NS["rdf"]
RDF_RES = "{%s}resource" % NS["rdf"]

YEAR = datetime.date.today().year
DIED_BY = YEAR - 61  # India: life + 60, from the following 1 January
OK_NO_DATES = {"anonymous", "unknown"}

# Gutenberg's "Category:" bookshelves -> the app's tags (short codes).
TAGS = {
    "Category: Crime, Thrillers and Mystery": "mys",
    "Category: Mythology, Legends & Folklore": "myt",
    "Category: Adventure": "adv",
    "Category: Romance": "rom",
    "Category: Science-Fiction & Fantasy": "sff",
    "Category: Philosophy & Ethics": "phi",
    "Category: Poetry": "poe",
    "Category: Short Stories": "sho",
    "Category: Children & Young Adult Reading": "kid",
    "Category: Humour": "hum",
    "Category: Classics of Literature": "cla",
    "Category: Novels": "nov",
    "Category: Horror": "hor",
    "Category: Biographies": "bio",
    "Category: History - General": "his",
    "Category: Religion/Spirituality": "rel",
    "Category: Psychiatry/Psychology": "psy",
    "Category: Travel Writing": "tra",
}
# Library shelves: key, title, subtitle, rule.
SHELVES = [
    ("most", "Most read", "Most downloaded classics", lambda b: True),
    ("short", "Short reads", "Under 2 hours", lambda b: b["words"] <= 30000),
    ("mys", "Mystery", "Detectives, crimes and thrillers", lambda b: "mys" in b["tags"]),
    ("adv", "Adventure", "Pirates, journeys and daring escapes", lambda b: "adv" in b["tags"]),
    ("rom", "Romance", "Love stories that lasted a century", lambda b: "rom" in b["tags"]),
    ("sff", "Sci-fi & fantasy", "The first time machines and other worlds", lambda b: "sff" in b["tags"]),
    ("hor", "Horror", "Read these with the lights on", lambda b: "hor" in b["tags"]),
    ("phi", "Philosophy", "Old ideas that still hold up", lambda b: "phi" in b["tags"]),
    ("poe", "Poetry", "A few pages at a time", lambda b: "poe" in b["tags"]),
    ("india", "From India", "Indian authors and stories", lambda b: b.get("india", False)),
    ("kid", "Young readers", "Children's classics, for any age", lambda b: "kid" in b["tags"]),
    ("hum", "Humour", "Old books that are still funny", lambda b: "hum" in b["tags"]),
]
SHELF_SIZE = 30
INDIA_AUTHORS = re.compile(r"Tagore|Naidu, Sarojini|Dutt, Toru|Dutt, Romesh|Chattopadhyay|Chatterjee, Bankim|Vivekananda|"
                           r"Kalidasa|K[aā]lid[aā]sa|Valmiki|Vyasa|Premchand|Aurobindo|Gandhi, Mahatma|Ramakrishna|Bose, Jagadis")

WORDS_PER_BYTE = 1 / 5.8  # plain-text size to words, after Gutenberg's ~19 KB of header and licence


def agent_info(el):
    """(display name, death year or None, is anonymous) of a pgterms:agent."""
    name = (el.findtext("pgterms:name", default="", namespaces=NS) or "").strip()
    death = el.findtext("pgterms:deathdate", default=None, namespaces=NS)
    try:
        death = int(death) if death else None
    except ValueError:
        death = None
    return name, death


def display_name(name):
    """'Doyle, Arthur Conan' -> 'Arthur Conan Doyle'; 'Tolstoy, Leo, graf' -> 'Leo Tolstoy';
    'Marcus Aurelius, Emperor of Rome' -> 'Marcus Aurelius'. Drops bracketed full names."""
    name = re.sub(r"\s*\(.*?\)", "", name).strip()
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if not parts:
        return ""
    if len(parts) >= 2:
        first = parts[1]
        surname_ok = len(parts[0].split()) <= 2
        given_ok = first[:1].isupper() and " of " not in first and not re.match(r"^(Jr|Sr|[IVX]+)\.?$", first)
        if surname_ok and given_ok:
            return first + " " + parts[0]
    return parts[0]


def clean_title(t):
    t = re.sub(r"\s*\$[a-z]\s*", " ", t)
    t = re.sub(r"\s+", " ", t.replace("\r", " ").replace("\n", ": ")).strip()
    t = t.replace(": :", ":")
    return t[:140]


def parse(data):
    root = ET.fromstring(data)
    ebook = root.find("pgterms:ebook", NS)
    if ebook is None:
        return None
    m = re.search(r"ebooks/(\d+)", ebook.get(RDF_ABOUT, ""))
    if not m:
        return None
    bid = int(m.group(1))
    typ = ebook.find("dcterms:type/rdf:Description/rdf:value", NS)
    if typ is None or typ.text != "Text":
        return None
    langs = [v.text for v in ebook.findall("dcterms:language/rdf:Description/rdf:value", NS)]
    if langs != ["en"]:
        return None
    rights = ebook.findtext("dcterms:rights", default="", namespaces=NS)
    if not rights.startswith("Public domain"):
        return None
    title = ebook.findtext("dcterms:title", default="", namespaces=NS)
    if not title:
        return None
    creators = [agent_info(a) for a in ebook.findall("dcterms:creator/pgterms:agent", NS)]
    translators = [agent_info(a) for a in ebook.findall("marcrel:trl/pgterms:agent", NS)]
    if not creators:
        return None
    for name, death in creators + translators:
        if death is None:
            if name.strip().lower() not in OK_NO_DATES:
                return None
        elif death > DIED_BY:
            return None
    shelves = [v.text or "" for v in ebook.findall("pgterms:bookshelf/rdf:Description/rdf:value", NS)]
    subjects = [v.text or "" for v in ebook.findall("dcterms:subject/rdf:Description/rdf:value", NS)]
    if any("Periodicals" in s for s in subjects) or any("Journals" in s or "Magazine" in s for s in shelves):
        return None
    has_epub = False
    txt_bytes = 0
    for f in ebook.findall("dcterms:hasFormat/pgterms:file", NS):
        about = f.get(RDF_ABOUT, "")
        fmts = [v.text or "" for v in f.findall("dcterms:format/rdf:Description/rdf:value", NS)]
        size = int(f.findtext("dcterms:extent", default="0", namespaces=NS) or 0)
        if about.endswith(".epub.noimages") or ("application/epub+zip" in fmts and "noimages" in about):
            has_epub = True
        if any(x.startswith("text/plain") for x in fmts) and "utf-8" in " ".join(fmts).lower():
            txt_bytes = max(txt_bytes, size)
        elif any(x.startswith("text/plain") for x in fmts) and not txt_bytes:
            txt_bytes = size
    if not has_epub or txt_bytes <= 0:
        return None
    words = int(max(0, txt_bytes - 19000) * WORDS_PER_BYTE)
    if words < 1200 or words > 700000:
        return None
    downloads = int(ebook.findtext("pgterms:downloads", default="0", namespaces=NS) or 0)
    if "Category: Sexuality & Erotica" in shelves:
        return None
    tags = {TAGS[s] for s in shelves if s in TAGS}
    if any("Horror" in s or "Ghost stories" in s for s in subjects):
        tags.add("hor")
    if any("Detective and mystery" in s for s in subjects):
        tags.add("mys")
    tags = sorted(tags)
    author = " & ".join(display_name(n) for n, _ in creators[:2] if n)
    india = bool(INDIA_AUTHORS.search(" ".join(n for n, _ in creators))) or any(
        re.search(r"\bIndia\b", s) and ("Fiction" in s or "Poetry" in s or "Folklore" in s) for s in subjects)
    about = ebook.findtext("pgterms:marc520", default="", namespaces=NS) or ""
    issued = ebook.findtext("dcterms:issued", default="", namespaces=NS) or ""
    return dict(id=bid, title=clean_title(title), author=author, words=words, downloads=downloads, tags=tags,
                india=india, about=about.strip(), issued=issued)


def short_about(text):
    """Gutenberg's summaries run long; keep the first two sentences, without the '(This is an automatically
    generated summary.)' note."""
    text = re.sub(r"\(This is an automatically generated summary\.?\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", text)
    out = " ".join(sentences[:2]).strip()
    return out[:420]


def tsv(s):
    return s.replace("\t", " ").replace("\n", " ").strip()


# ---------- Cover check (shelves only): Gutenberg makes a placeholder cover when a book has none ----------
def is_placeholder(jpeg_bytes):
    """True for Gutenberg's generated covers: a white band with the title on top and big flat blocks of pure
    colour below. Real scans and drawn covers have texture. See is_placeholder_pixels."""
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        im = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB").resize((24, 36))
    except Exception:
        return False
    return is_placeholder_pixels([im.getpixel((x, y)) for y in range(36) for x in range(24)], 24, 36)


def is_placeholder_pixels(px, w, h):
    """Same rule as the app (Covers.isPlaceholder): the top 22% is mostly white (the title band) and the lower
    half is almost all strongly saturated colour (the flat pattern). Scans and drawn covers fail one or both."""
    def at(x, y):
        return px[y * w + x]
    top = [at(x, y) for y in range(1, int(h * 0.22)) for x in range(w)]
    white_top = sum(1 for c in top if min(c) > 215) / max(1, len(top))
    low = [at(x, y) for y in range(int(h * 0.42), int(h * 0.92)) for x in range(w)]
    vivid = sum(1 for c in low if max(c) - min(c) > 110) / max(1, len(low))
    return white_top > 0.6 and vivid > 0.85


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def main(out_dir, rdf_path=None, check_covers=True):
    if rdf_path is None:
        rdf_path = "rdf-files.tar.bz2"
        if not os.path.exists(rdf_path):
            print("downloading", RDF_URL)
            with open(rdf_path, "wb") as f:
                f.write(fetch(RDF_URL, timeout=600))
    books = []
    seen = 0
    with tarfile.open(rdf_path, "r:bz2") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".rdf"):
                continue
            seen += 1
            try:
                b = parse(tar.extractfile(member).read())
            except ET.ParseError:
                b = None
            if b:
                books.append(b)
    books.sort(key=lambda b: (-b["downloads"], b["id"]))
    print(f"{seen} records, {len(books)} books kept")

    os.makedirs(os.path.join(out_dir, "about"), exist_ok=True)
    with open(os.path.join(out_dir, "index.tsv"), "w", encoding="utf-8") as f:
        for b in books:
            f.write(f"{b['id']}\t{tsv(b['title'])}\t{tsv(b['author'])}\t{b['words']}\t{','.join(b['tags'])}\n")

    shards = collections.defaultdict(list)
    for b in books:
        if b["about"]:
            shards[b["id"] // 100].append((b["id"], short_about(b["about"])))
    for n, rows in shards.items():
        with open(os.path.join(out_dir, "about", f"{n}.tsv"), "w", encoding="utf-8") as f:
            for bid, text in sorted(rows):
                f.write(f"{bid}\t{tsv(text)}\n")

    # Shelves: the most read books of each kind, real covers only.
    cover_cache_path = os.path.join(out_dir, "covers.tsv")
    cache = {}
    if os.path.exists(cover_cache_path):
        for line in open(cover_cache_path, encoding="utf-8"):
            p = line.rstrip("\n").split("\t")
            if len(p) == 2:
                cache[int(p[0])] = p[1]

    def real_cover(bid):
        if not check_covers:
            return True
        if bid not in cache:
            try:
                cache[bid] = "p" if is_placeholder(fetch(f"{MIRROR}/{bid}/pg{bid}.cover.small.jpg")) else "r"
                time.sleep(0.2)
            except Exception:
                return True  # unknown: keep it; the app checks again when it draws the cover
        return cache[bid] == "r"

    # New releases spike in Gutenberg's 30-day download counts; shelves wait until a book is half a year old.
    fresh = (datetime.date.today() - datetime.timedelta(days=180)).isoformat()
    lines = []
    for key, title, sub, rule in SHELVES:
        ids = []
        for b in books:
            if len(ids) >= SHELF_SIZE:
                break
            if rule(b) and b["tags"] and b["issued"] < fresh and not re.search(r"\bVol(\.|ume)\s|\[Vol", b["title"]) and real_cover(b["id"]):
                ids.append(b["id"])
        if len(ids) >= 8:
            lines.append(f"{key}\t{title}\t{sub}\t{','.join(map(str, ids))}")
        print(f"shelf {key}: {len(ids)}")
    with open(os.path.join(out_dir, "shelves.tsv"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(cover_cache_path, "w", encoding="utf-8") as f:
        for bid in sorted(cache):
            f.write(f"{bid}\t{cache[bid]}\n")
    with open(os.path.join(out_dir, "meta.txt"), "w", encoding="utf-8") as f:
        f.write(f"built\t{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ}\nbooks\t{len(books)}\ndied_by\t{DIED_BY}\n")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "books/v1"
    rdf = sys.argv[2] if len(sys.argv) > 2 else None
    main(out, rdf, check_covers="--no-covers" not in sys.argv)
