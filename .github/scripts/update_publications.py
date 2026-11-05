#!/usr/bin/env python3
"""
Compare publications/index.html with OpenAlex and propose changes.

Run by .github/workflows/update-publications.yml once a month. It edits
publications/index.html in place and writes a summary to pr-body.md; the
workflow then opens a pull request for you to review. Nothing is published
until you merge that pull request.

It only ever:
  * adds papers that are not on the page yet, and
  * moves a preprint to the right section once it has been published.
It never deletes or rewrites anything else.

Papers you don't want listed: add their DOI (or OpenAlex ID) to
.github/scripts/publications-ignore.txt.

Standard library only; run locally with:  python3 .github/scripts/update_publications.py
"""
import difflib
import html
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

ORCID = "0000-0001-8708-7409"
MY_NAME = "Hildeberto Jardón-Kojakhmetov"

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "publications" / "index.html"
IGNORE = Path(__file__).with_name("publications-ignore.txt")
SUMMARY = ROOT / "pr-body.md"

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
    added, moved, check = [], [], []
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
            if key == "preprint" and section != "preprint":
                sections["preprint"].remove(li)
                new_li = render(w, section)
                insert_by_year(sections[section], new_li)
                moved.append((old_title, w, section))
                handled_titles.append(title)
                if needs_check(w):
                    check.append(title)
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

    if not added and not moved:
        print("Publications are up to date.")
        return

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
