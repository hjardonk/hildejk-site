#!/usr/bin/env python3
"""
Compare publications/index.html with OpenAlex and propose changes.

Run by .github/workflows/update-publications.yml once a month. It edits
publications/index.html in place and writes a summary to pr-body.md; the
workflow then opens a pull request for you to review. Nothing is published
until you merge that pull request.

It only ever:
  * adds papers that are not on the page yet,
  * moves a preprint to the right section once it has been published, and
  * adds a free "PDF" link (open-access or arXiv version) to papers without one, and
  * refills the "Papers on this topic" lists on the Research page.
It never deletes or rewrites anything else.

Papers you don't want listed: add their DOI (or OpenAlex ID) to
.github/scripts/publications-ignore.txt.
A paper that should never get a PDF link: put <!-- no pdf --> inside its <li>.
Which papers go under which research theme: .github/scripts/research-topics.txt.

Standard library only; run locally with:  python3 .github/scripts/update_publications.py
"""
import configparser
import difflib
import html
import json
import math
import re
import sys
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

ORCID = "0000-0001-8708-7409"
MY_NAME = "Hildeberto Jardón-Kojakhmetov"

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "publications" / "index.html"
IGNORE = Path(__file__).with_name("publications-ignore.txt")
SUMMARY = ROOT / "pr-body.md"
RESEARCH = ROOT / "research" / "index.html"
TOPICS = Path(__file__).with_name("research-topics.txt")

# Filling gaps on the Research page: how many similar papers vote, and the
# similarity (0 to 1) the closest one needs before a paper gets a best-guess theme.
NEIGHBOURS = 5
MIN_SIMILARITY = 0.15

SECTION_NAMES = {
    "journal": "Journal articles",
    "conference": "Conference papers",
    "preprint": "Preprints",
    "books": "Books and book chapters",
    "thesis": "PhD thesis",
}


# ----------------------------------------------------------------- helpers

def norm_title(t):
    t = re.sub(r"<[^>]+>", "", html.unescape(t or ""))
    return re.sub(r"[^a-z0-9]", "", t.lower())


def same_title(a, b):
    a, b = norm_title(a), norm_title(b)
    return bool(a) and bool(b) and (a == b or difflib.SequenceMatcher(None, a, b).ratio() > 0.92)


def ids_in(text):
    """DOIs, arXiv IDs and OpenAlex IDs found anywhere in a string."""
    text = html.unescape(text or "").lower()
    found = set()
    for doi in re.findall(r"10\.\d{4,9}/[^\s\"<>&]+", text):
        doi = doi.rstrip(".,;)")
        found.add("doi:" + doi)
        m = re.match(r"10\.48550/arxiv\.(.+)", doi)
        if m:
            found.add("arxiv:" + re.sub(r"v\d+$", "", m.group(1)))
    for ax in re.findall(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?(?=[\s\"<>]|$)", text):
        found.add("arxiv:" + ax)
    for ax in re.findall(r"arxiv:(\d{4}\.\d{4,5})", text):
        found.add("arxiv:" + ax)
    for w in re.findall(r"\bw\d{6,}\b", text):
        found.add("openalex:" + w)
    return found


def fetch_works():
    works, cursor = [], "*"
    while cursor:
        url = (f"https://api.openalex.org/works?filter=author.orcid:{ORCID}"
               f"&per-page=100&cursor={cursor}")
        req = urllib.request.Request(url, headers={"User-Agent": "hildejk.xyz publication check"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        works += data["results"]
        cursor = data["meta"].get("next_cursor")
        if not data["results"]:
            break
    return works


# ----------------------------------------------------------------- the page

SECTION_RE = re.compile(
    r'(<h2 id="(?P<key>\w+)">.*?</h2>.*?<[ou]l class="publications"[^>]*>)(?P<items>.*?)(</[ou]l>)', re.S)
ITEM_RE = re.compile(r"<li>.*?</li>", re.S)


def read_page():
    text = PAGE.read_text()
    sections = {}
    for m in SECTION_RE.finditer(text):
        sections[m.group("key")] = ITEM_RE.findall(m.group("items"))
    return text, sections


def item_title(li):
    m = re.search(r'class="pub-title">(.*?)</div>', li, re.S)
    return re.sub(r"<[^>]+>", "", m.group(1)) if m else ""


def item_year(li):
    years = re.findall(r"\((\d{4})\)", li)
    return int(years[-1]) if years else 0


def write_page(text, sections):
    def repl(m):
        items = sections.get(m.group("key"))
        if items is None:
            return m.group(0)
        body = "\n" + "\n\n".join("  " + li.strip() for li in items) + "\n"
        return m.group(1) + body + m.group(4)
    PAGE.write_text(SECTION_RE.sub(repl, text))


def insert_by_year(items, li):
    y = item_year(li)
    for i, other in enumerate(items):
        if item_year(other) <= y:
            items.insert(i, li)
            return
    items.append(li)


# ----------------------------------------------------------------- OpenAlex works

ALLOWED_TAGS = ("i", "sub", "sup", "b")


def clean_title(t):
    t = html.escape(html.unescape(t or "").strip(), quote=False)
    for tag in ALLOWED_TAGS:
        t = t.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return t.replace("--", "–")


def best_location(w):
    """Prefer the journal/conference version over a repository copy."""
    for loc in w.get("locations") or []:
        if ((loc.get("source") or {}).get("type")) in ("journal", "conference", "book series", "ebook platform"):
            return loc
    return w.get("primary_location") or {}


def section_for(w):
    loc = best_location(w)
    stype = (loc.get("source") or {}).get("type")
    t = w.get("type")
    if t in ("book-chapter", "book") or (t == "other" and stype == "book series"):
        return "books"
    if stype == "repository" or t == "preprint":
        return "preprint"
    if t == "conference-paper" or stype == "conference":
        return "conference"
    if t == "dissertation":
        return "thesis"
    if t in ("article", "review", "letter", "other") and stype == "journal":
        return "journal"
    return None  # errata, datasets, editorials, ...


def work_ids(w):
    found = {"openalex:" + w["id"].rsplit("/", 1)[-1].lower()}
    found |= ids_in(w.get("doi") or "")
    for loc in w.get("locations") or []:
        found |= ids_in(loc.get("landing_page_url") or "")
        found |= ids_in(loc.get("pdf_url") or "")
    return found


def work_link(w):
    loc = best_location(w)
    url = loc.get("landing_page_url") or ""
    if "doi.org/" in url:
        return url
    return w.get("doi") or url


def free_pdf(w):
    """Most reliable free full text: arXiv, then a repository copy, then the publisher.

    Publisher "free PDF" links often sit behind bot checks or expire, so they come last.
    """
    if w["id"] in _pdf_cache:
        return _pdf_cache[w["id"]]
    ax = sorted(i[6:] for i in work_ids(w) if i.startswith("arxiv:"))
    found = f"https://arxiv.org/pdf/{ax[-1]}" if ax else None
    if not found:
        locs = [l for l in [w.get("best_oa_location")] + (w.get("locations") or []) if l]
        repo = [l for l in locs if (l.get("source") or {}).get("type") == "repository"]
        for loc in repo + [l for l in locs if l not in repo]:
            if loc.get("is_oa") and loc.get("pdf_url") and is_real_pdf(loc["pdf_url"]):
                found = loc["pdf_url"]
                break
    _pdf_cache[w["id"]] = found
    return found


_pdf_cache = {}


def is_real_pdf(url):
    """True only if the link downloads a PDF for a machine, not a login page or bot check."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hildejk.xyz link check)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(5) == b"%PDF-"
    except Exception:
        return False


def pdf_link(url):
    return f' · <a class="pdf" href="{html.escape(url)}">PDF</a>'


def has_pdf(li):
    return 'class="pdf"' in li or "<!-- no pdf -->" in li


def add_pdf(li, url):
    return re.sub(r'(<div class="pub-venue">.*?)(</div>)',
                  lambda m: m.group(1) + pdf_link(url) + m.group(2), li, count=1, flags=re.S)


def render(w, section):
    authors = []
    for a in w.get("authorships") or []:
        orcid = (a.get("author") or {}).get("orcid") or ""
        name = (a.get("author") or {}).get("display_name") or a.get("raw_author_name") or ""
        if orcid.endswith(ORCID):
            authors.append(f"<b>{MY_NAME}</b>")
        else:
            authors.append(html.escape(name, quote=False))
    year = w.get("publication_year")
    loc = best_location(w)
    if section == "preprint":
        ax = sorted(i for i in work_ids(w) if i.startswith("arxiv:"))
        venue = f"arXiv:{ax[0][6:]} ({year})" if ax else f"<i>{html.escape((loc.get('source') or {}).get('display_name') or 'Preprint')}</i> ({year})"
    else:
        b = w.get("biblio") or {}
        bits = [b.get("volume")]
        fp, lp = b.get("first_page"), b.get("last_page")
        bits.append(f"{fp}–{lp}" if fp and lp and fp != lp else fp)
        detail = ", ".join(x for x in bits if x)
        src = html.escape((loc.get("source") or {}).get("display_name") or "", quote=False)
        venue = f"<i>{src}</i>{' ' + detail if detail else ''} ({year})"
    pdf = free_pdf(w)
    if pdf:
        venue += pdf_link(pdf)
    link = work_link(w)
    title = clean_title(w.get("title") or w.get("display_name"))
    title_html = f'<a href="{html.escape(link)}">{title}</a>' if link else title
    return (f"<li>\n"
            f"    <div class=\"pub-title\">{title_html}</div>\n"
            f"    <div class=\"pub-authors\">{', '.join(authors)}</div>\n"
            f"    <div class=\"pub-venue\">{venue}</div>\n"
            f"  </li>")


def needs_check(w):
    t = w.get("title") or ""
    return bool(re.search(r"[$\\{}]|&lt;|mathcal|mathbb", t))


# ----------------------------------------------------------------- Research page

NO_THEME = "no theme"
BLOCK_RE = re.compile(r"(<!-- papers:(?P<key>[\w-]+) .*?-->)(.*?)(<!-- /papers:(?P=key) -->)", re.S)
STOPWORDS = set("""the and for with from that this are was were which have has been their its into
    can may also than then these those such using use used via our two one new study show shows
    between under over based model models system systems analysis approach results paper""".split())


def squash(s):
    """Lower case, with hyphens, dashes and punctuation turned into single spaces."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", html.unescape(s or "").lower()).split())


def split_list(value):
    return [squash(x) for x in re.split(r"[,\n]", value or "") if squash(x)]


def load_topics():
    cp = configparser.ConfigParser(interpolation=None, delimiters=(":",), comment_prefixes=("#",))
    cp.read(TOPICS, encoding="utf-8")
    return {key: {
        "name": cp[key].get("name", key).strip(),
        "keywords": split_list(cp[key].get("keywords")),
        "title_keywords": split_list(cp[key].get("title keywords")),
        "always": [x.strip() for x in cp[key].get("always", "").splitlines() if x.strip()],
        "never": [x.strip() for x in cp[key].get("never", "").splitlines() if x.strip()],
    } for key in cp.sections() if key != NO_THEME}


def load_no_theme():
    cp = configparser.ConfigParser(interpolation=None, delimiters=(":",), comment_prefixes=("#",))
    cp.read(TOPICS, encoding="utf-8")
    return [x.strip() for x in cp.get(NO_THEME, "papers", fallback="").splitlines() if x.strip()]


def abstract_of(w):
    positions = sorted((i, word) for word, where in (w.get("abstract_inverted_index") or {}).items() for i in where)
    return " ".join(word for _, word in positions)


def paper_records(sections, works):
    """Every paper on the publications page, with its OpenAlex data when there is a match."""
    records = []
    for items in sections.values():
        for li in items:
            ids, title = ids_in(li), item_title(li)
            found = [w for w in works if (work_ids(w) & ids) or same_title(title, w.get("title"))]
            title_html = re.search(r'class="pub-title">(.*?)</div>', li, re.S).group(1).strip()
            venue_html = re.search(r'class="pub-venue">(.*?)</div>', li, re.S).group(1)
            italic = re.search(r"<i>(.*?)</i>", venue_html, re.S)
            venue = italic.group(1) if italic else ("arXiv" if venue_html.startswith("arXiv") else "PhD thesis")
            records.append({
                "title": html.unescape(title).strip(), "title_html": title_html, "venue": venue,
                "year": item_year(li), "ids": ids,
                "abstract": next((a for a in map(abstract_of, found) if a), ""),
                "terms": [t.get("display_name", "") for w in found for t in (w.get("topics") or [])]
                         + [k.get("display_name", "") for w in found for k in (w.get("keywords") or [])],
                "refs": sorted({r for w in found for r in (w.get("referenced_works") or [])}),
                "themes": [], "guessed": None,
            })
    return records


def has_keyword(keywords, text):
    text = " " + text
    return any(" " + k in text for k in keywords)


def listed(paper, entries):
    return any(("doi:" + e.lower()) in paper["ids"] or squash(e) in squash(paper["title"]) for e in entries)


def word_counts(paper):
    """Weighted words of a paper. Shared references count as words too."""
    counts = Counter()
    for weight, text in ((3, paper["title"]), (1, paper["abstract"]), (2, " ".join(paper["terms"]))):
        for word in squash(text).split():
            if len(word) > 2 and word not in STOPWORDS:
                counts[word] += weight
    for ref in paper["refs"]:
        counts["ref:" + ref] += 2
    return counts


def similarity_vectors(papers):
    counts = [word_counts(p) for p in papers]
    seen_in = Counter(word for c in counts for word in c)
    vectors = []
    for c in counts:
        v = {w: n * math.log(1 + len(papers) / seen_in[w]) for w, n in c.items()}
        size = math.sqrt(sum(x * x for x in v.values())) or 1
        vectors.append({w: x / size for w, x in v.items()})
    return vectors


def classify(papers, topics):
    """Keywords first; papers left without a theme borrow one from their closest neighbours."""
    no_theme = load_no_theme()
    for p in papers:
        p["excluded"] = listed(p, no_theme)
        if p["excluded"]:
            continue
        title, text = squash(p["title"]), squash(p["title"] + " " + p["abstract"])
        for key, t in topics.items():
            if listed(p, t["never"]):
                continue
            if listed(p, t["always"]) or has_keyword(t["keywords"], text) or has_keyword(t["title_keywords"], title):
                p["themes"].append(key)

    vectors = similarity_vectors(papers)
    for i, p in enumerate(papers):
        if p["themes"] or p["excluded"]:
            continue
        near = []
        for j, q in enumerate(papers):
            if q["themes"] and not q["guessed"]:
                sim = sum(x * vectors[j].get(w, 0) for w, x in vectors[i].items())
                near.append((sim, j))
        near = sorted(near, reverse=True)[:NEIGHBOURS]
        if not near or near[0][0] < MIN_SIMILARITY:
            continue
        votes = Counter({key: 0 for key in topics})  # ties go to the theme listed first in the settings
        for sim, j in near:
            if sim >= near[0][0] / 2:  # only papers about as close as the closest one get a vote
                for key in papers[j]["themes"]:
                    if not listed(p, topics[key]["never"]):
                        votes[key] += sim
        best, score = votes.most_common(1)[0]
        if score > 0:
            p["themes"] = [best]
            p["guessed"] = best
    return papers


def render_block(papers):
    if not papers:
        return "\n"
    rows = "\n".join(
        f'    <li>{p["title_html"]} <span class="related-venue">· {p["venue"]}, {p["year"]}</span></li>' for p in papers)
    return (f'\n<details class="related">\n  <summary>Papers on this topic ({len(papers)})</summary>\n'
            f"  <ul>\n{rows}\n  </ul>\n</details>\n")


def update_research(sections, works):
    """Refill the paper lists on the Research page. Returns (theme, paper, guessed) for additions."""
    if not TOPICS.exists() or not RESEARCH.exists():
        return []
    topics = load_topics()
    papers = sorted(classify(paper_records(sections, works), topics), key=lambda p: -p["year"])
    text = RESEARCH.read_text()
    changes = []

    def refill(m):
        key = m.group("key")
        if key not in topics:
            return m.group(0)
        members = [p for p in papers if key in p["themes"]]
        known = re.sub(r"\s+", " ", m.group(3))
        for p in members:
            if re.sub(r"\s+", " ", p["title_html"]) not in known:
                changes.append((topics[key]["name"], p["title"], p["guessed"] == key))
        return m.group(1) + render_block(members) + m.group(4)

    new_text = BLOCK_RE.sub(refill, text)
    if new_text != text:
        RESEARCH.write_text(new_text)
        changes = changes or [("", "", False)]  # order, counts or removals changed
    return changes


# ----------------------------------------------------------------- main

def main():
    text, sections = read_page()
    if not sections:
        sys.exit("Could not find the publication lists in publications/index.html")

    ignore = set()
    if IGNORE.exists():
        for line in IGNORE.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ignore |= ids_in(line) or {line.lower()}

    page_items = [(key, li, ids_in(li), item_title(li)) for key, items in sections.items() for li in items]

    works = sorted(fetch_works(), key=lambda w: (w.get("publication_year") or 0), reverse=True)
    added, moved, check, pdfs = [], [], [], []
    handled_titles = []

    for w in works:
        section = section_for(w)
        ids = work_ids(w)
        title = w.get("title") or ""
        if section is None or ids & ignore:
            continue
        if any(same_title(title, t) for t in handled_titles):
            continue  # another OpenAlex record of a paper handled in this run

        match = next((p for p in page_items if (p[2] & ids) or same_title(p[3], title)), None)

        if match:
            key, li, _, old_title = match
            if li not in sections[key]:
                continue  # already changed earlier in this run
            if key == "preprint" and section != "preprint":
                sections["preprint"].remove(li)
                new_li = render(w, section)
                insert_by_year(sections[section], new_li)
                moved.append((old_title, w, section))
                handled_titles.append(title)
                if needs_check(w):
                    check.append(title)
            elif not has_pdf(li) and free_pdf(w):
                items = sections[key]
                items[items.index(li)] = add_pdf(li, free_pdf(w))
                pdfs.append(old_title)
            continue

        # Not on the page yet.  Only suggest preprints that are recent and on arXiv:
        # older ones and university-repository copies are almost always versions of
        # papers you already list (or left out on purpose).
        if section == "preprint" and (
                not any(i.startswith("arxiv:") for i in ids)
                or (w.get("publication_year") or 0) < date.today().year - 1):
            continue
        # Skip the preprint copy if the published one is also new.
        if section == "preprint" and any(
                same_title(title, o.get("title")) and section_for(o) not in (None, "preprint") for o in works):
            continue
        if section not in sections:
            continue
        insert_by_year(sections[section], render(w, section))
        added.append((w, section))
        handled_titles.append(title)
        if needs_check(w):
            check.append(title)

    research = update_research(sections, works)

    if not added and not moved and not pdfs and not research:
        print("Publications are up to date.")
        return

    if added or moved or pdfs:
        write_page(text, sections)

    def oa_id(w):
        return w["id"].rsplit("/", 1)[-1]

    lines = ["Monthly check against [OpenAlex](https://openalex.org) found changes to your publication list.", ""]
    if moved:
        lines += ["### Preprints that are now published", ""]
        for old, w, sec in moved:
            src = (best_location(w).get("source") or {}).get("display_name") or ""
            lines.append(f"- **{old.strip()}** → *{src}* ({w.get('publication_year')})")
        lines.append("")
    if added:
        lines += ["### New papers", ""]
        for w, sec in added:
            lines.append(f"- **{html.unescape(w.get('title') or '')}** — {SECTION_NAMES[sec]}, "
                         f"{w.get('publication_year')} · ignore ID: `{(w.get('doi') or oa_id(w)).replace('https://doi.org/', '')}`")
        lines.append("")
    if pdfs:
        lines += ["### Free PDF links added", ""]
        lines += [f"- {t.strip()}" for t in pdfs]
        lines += ["", "To stop a paper from getting a PDF link, delete the link and put `<!-- no pdf -->` inside its `<li>`.", ""]
    themed = [c for c in research if c[0]]
    if themed:
        lines += ["### Research page: papers added under a theme", ""]
        lines += [f"- *{name}*: {title}" + (" **(best guess: no keyword matched)**" if guess else "")
                  for name, title, guess in themed]
        lines += ["", "To correct a theme, edit `always` or `never` in `.github/scripts/research-topics.txt`.", ""]
    elif research:
        lines += ["### Research page", "", "The paper lists were refreshed (no new papers).", ""]
    if check:
        lines += ["### Please check these titles", "",
                  "They contain math or special characters that may not display well:", ""]
        lines += [f"- {html.unescape(t)}" for t in check]
        lines.append("")
    lines += ["---",
              "**What to do:** look at the *Files changed* tab. If it looks right, click **Merge pull request**; "
              "the website updates a minute later.",
              "",
              "- To fix a title or author list, open `publications/index.html` from the *Files changed* tab, "
              "edit it, and commit to this same branch before merging.",
              "- If a paper should never be listed, copy its ignore ID into `.github/scripts/publications-ignore.txt`, "
              "remove it from the page in this pull request, and merge.",
              "- If none of this is useful, just close the pull request."]
    SUMMARY.write_text("\n".join(lines) + "\n")
    print(SUMMARY.read_text())


if __name__ == "__main__":
    main()
