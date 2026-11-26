"""
build_data.py: turns the raw CSV files into the three JSON files the website loads.

Run it from the folder that holds the CSV files (or pass that folder as the
first argument), e.g.

    python build_data.py
    python build_data.py path/to/csv/folder

It reads:
    DOIVenueTitleYearAuthors.csv  every paper: DOI, venue, title, authors, year
    institutions_located.csv      every institution: its DOIs, collaborators,
                                  country and coordinates
    extra_coordinates.csv         (optional) country + coordinates for
                                  institutions that have none in
                                  institutions_located.csv (taken from MAPv4)
    authors_merged.csv            every author: links, points, affiliations,
                                  emails, DOIs
    PeopleToBeRanked.csv          the authors that appear in the ranking
    top_collaborators.csv         top 3 co-authors of every author

and writes, next to this script:
    papers.json     list of papers (title, year, venue code, doi)
    map_data.json   venues + institutions (used by the map)
    authors.json    every author's profile (used by profiles and ranking)

DOIs are the key that links the files. They are compared in lower case,
because some files write them in lower case and some do not.

Email addresses are NEVER copied into the JSON files: an author only gets
"hasEmail": true or false. So the CSV files themselves (authors_merged.csv
contains emails) should not be uploaded to a public repository.
"""

import csv
import json
import os
import re
import sys
import unicodedata
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_FOLDER = sys.argv[1] if len(sys.argv) > 1 else HERE

# Raw venue names in the CSV -> the short venue code shown on the website.
# Several raw names are merged into one venue (workshops, companions,
# adjuncts), and "CIU" is a typo of "CUI".
VENUE_CODES = {
    "Int. J. Hum. Comput. Interact.": "IJHCI",
    "IEEE Trans. Affect. Comput.": "IEEE TAffC",
    "Comput. Hum. Behav.": "CHB",
    "ICMI": "ICMI",
    "ICMI Companion": "ICMI",
    "CUI": "CUI",
    "CIU": "CUI",
    "IVA": "IVA",
    "IVA (Adjunct)": "IVA",
    "Int. J. Hum. Comput. Stud.": "IJHCS",
    "ACII": "ACII",
    "ACIIW": "ACII",
    "ACII (Workshops and Demos)": "ACII",
    "ACM Trans. Interact. Intell. Syst.": "TiiS",
    "J. Multimodal User Interfaces": "JMUI",
}

# Full name of each venue code, shown in the venue filter.
VENUE_NAMES = {
    "IJHCI": "Int. Journal of Human-Computer Interaction",
    "IEEE TAffC": "IEEE Transactions on Affective Computing",
    "ICMI": "Int. Conference on Multimodal Interaction",
    "CHB": "Computers in Human Behavior",
    "ACII": "Affective Computing and Intelligent Interaction",
    "CUI": "Conversational User Interfaces",
    "IVA": "Intelligent Virtual Agents",
    "IJHCS": "Int. Journal of Human-Computer Studies",
    "TiiS": "ACM Transactions on Interactive Intelligent Systems",
    "JMUI": "Journal on Multimodal User Interfaces",
}

# Some authors have several ORCID iDs, and usually only one of them is a
# real, filled-in ORCID profile. For those authors the script asks ORCID's
# public API (needs an internet connection) which iD has a profile with the
# author's name, and keeps only that one. If it can't tell (no connection,
# or several/none match), all iDs are kept and the profile lists them as
# ORCID 1, ORCID 2, ... Set this to False to skip the check.
CHECK_MULTIPLE_ORCIDS = True
ORCID_ID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")

# How many top authors an institution popup lists.
TOP_AUTHORS_PER_INSTITUTION = 5

# Institution rows that are not a real institution and are left out.
SKIP_INSTITUTIONS = {"NOT FOUND IN OPENALEX"}


def read_csv(name, delimiter, encoding="utf-8-sig"):
    """Reads a CSV file into a list of dicts (one per row)."""
    with open(os.path.join(CSV_FOLDER, name), encoding=encoding, newline="") as f:
        return list(csv.DictReader(f, delimiter=delimiter))


def split_list(text, separator):
    """'a; b; ;c' -> ['a', 'b', 'c']"""
    return [part.strip() for part in text.split(separator) if part.strip()]


def parse_number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def plain_words(text):
    """'André-Marie' -> {'andre', 'marie'} (lower case, no accents)."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return set(re.findall(r"[a-z]+", text))


orcid_cache = {}


def orcid_record_name(orcid_id):
    """
    The name on an ORCID profile as a set of words, an empty set when the
    profile exists but shows no name, or None when it doesn't exist or
    ORCID can't be reached.
    """
    if orcid_id in orcid_cache:
        return orcid_cache[orcid_id]
    result = None
    try:
        request = urllib.request.Request(
            f"https://pub.orcid.org/v3.0/{orcid_id}/person", headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            person = json.load(response)
        name = person.get("name") or {}
        parts = [
            ((name.get("given-names") or {}).get("value") or ""),
            ((name.get("family-name") or {}).get("value") or ""),
            ((name.get("credit-name") or {}).get("value") or ""),
        ]
        result = plain_words(" ".join(parts))
    except Exception:
        result = None
    orcid_cache[orcid_id] = result
    return result


def choose_orcids(author_name, orcid_text):
    """
    Returns the list of ORCID links to show for an author. With several
    iDs, keeps the one whose ORCID profile carries the author's name
    (see CHECK_MULTIPLE_ORCIDS).
    """
    ids = list(dict.fromkeys(ORCID_ID.findall(orcid_text)))
    if len(ids) > 1 and CHECK_MULTIPLE_ORCIDS:
        wanted = plain_words(author_name)
        names = {i: orcid_record_name(i) for i in ids}
        # A profile matches when it shares the author's last name word(s).
        last = plain_words(author_name.split()[-1]) if author_name.split() else set()
        matching = [i for i, words in names.items() if words and last and last <= words]
        if len(matching) != 1:
            matching = [i for i, words in names.items() if words and len(words & wanted) >= 2]
        if len(matching) != 1:
            matching = [i for i, words in names.items() if words is not None and words]
        if len(matching) == 1:
            ids = matching
    return [f"https://orcid.org/{i}" for i in ids]


# ---------- Papers ----------

paper_rows = read_csv("DOIVenueTitleYearAuthors.csv", ",")
papers = []
paper_index = {}  # lower-case DOI -> position in `papers`
unknown_venues = set()

for row in paper_rows:
    raw_venue = row["Venue"].strip()
    code = VENUE_CODES.get(raw_venue)
    if code is None:
        unknown_venues.add(raw_venue)
        code = raw_venue
    doi = row["DOI"].strip()
    paper_index[doi.lower()] = len(papers)
    papers.append({"title": row["Title"].strip(), "year": int(row["Year"]), "venue": code, "doi": doi})

if unknown_venues:
    print("WARNING: venues missing from VENUE_CODES (shown with their raw name):", sorted(unknown_venues))


def paper_ids(doi_text, separator=";"):
    """Turns a list of DOIs into a sorted list of paper positions (unknown DOIs are skipped)."""
    ids = {paper_index[d.lower()] for d in split_list(doi_text, separator) if d.lower() in paper_index}
    return sorted(ids)


venue_totals = {}
for paper in papers:
    venue_totals[paper["venue"]] = venue_totals.get(paper["venue"], 0) + 1

venues = [
    {"code": code, "name": VENUE_NAMES.get(code, code), "papers": count}
    for code, count in sorted(venue_totals.items(), key=lambda item: -item[1])
]


# ---------- Authors ----------

ranked_rows = read_csv("PeopleToBeRanked.csv", ";", encoding="latin-1")
ranked_names = {row["author"].strip() for row in ranked_rows}

collaborator_rows = {row["author"].strip(): row for row in read_csv("top_collaborators.csv", ";")}

author_rows = read_csv("authors_merged.csv", ";")
authors = []
for row in author_rows:
    name = row["author"].strip()
    collab_row = collaborator_rows.get(name, {})
    collaborators = []
    for i in (1, 2, 3):
        other = (collab_row.get(f"collaborator_{i}") or "").strip()
        if other:
            collaborators.append({"name": other, "shared": int(collab_row.get(f"shared_papers_{i}") or 0)})

    author = {
        "name": name,
        "orcid": choose_orcids(name, row["orcid"]),
        "scholar": row["google scholar"].strip(),
        "dblp": row["dblp page"].strip(),
        "hasEmail": row["emails"].strip() != "",
        "affiliations": split_list(row["affiliation"], "|"),
        "papers": paper_ids(row["doi"]),
        "first": int(row["first"]),
        "middle": int(row["middle"]),
        "last": int(row["last"]),
        "points": int(row["points"]),
        "collaborators": collaborators,
    }
    # Drop empty links so the JSON stays small; the website treats a
    # missing link the same as an empty one.
    for key in ("orcid", "scholar", "dblp"):
        if not author[key]:
            del author[key]
    authors.append(author)

missing_ranked = ranked_names - {a["name"] for a in authors}
if missing_ranked:
    print("WARNING: ranked people not found in authors_merged.csv:", sorted(missing_ranked))

# Ranks: only people in PeopleToBeRanked.csv get one. Equal points share a
# rank (e.g. 1, 2, 2, 4).
ranked = sorted((a for a in authors if a["name"] in ranked_names), key=lambda a: -a["points"])
for position, author in enumerate(ranked):
    if position > 0 and author["points"] == ranked[position - 1]["points"]:
        author["rank"] = ranked[position - 1]["rank"]
    else:
        author["rank"] = position + 1

authors.sort(key=lambda a: a["name"].lower())


# ---------- Institutions ----------

institution_rows = read_csv("institutions_located.csv", ",")

# Coordinates for institutions that institutions_located.csv could not place.
extra_coordinates = {}
if os.path.exists(os.path.join(CSV_FOLDER, "extra_coordinates.csv")):
    extra_coordinates = {row["institution"].strip(): row for row in read_csv("extra_coordinates.csv", ";")}
filled_in = 0
institutions = []
for row in institution_rows:
    name = row["institution"].strip()
    if name in SKIP_INSTITUTIONS:
        continue

    country = row["country_code"].strip()
    lat = parse_number(row["latitude"])
    lon = parse_number(row["longitude"])
    if (lat is None or lon is None) and name in extra_coordinates:
        extra = extra_coordinates[name]
        lat, lon = parse_number(extra["latitude"]), parse_number(extra["longitude"])
        country = country or extra["country"].strip()
        filled_in += 1
    has_coords = lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180

    # "Sorbonne Université (47); Institut ... (37)" -> [{name, shared}]
    collaborators = []
    for part in split_list(row["top_collaborators"], ";")[:3]:
        if part.endswith(")") and " (" in part:
            other, count = part[:-1].rsplit(" (", 1)
            collaborators.append({"name": other.strip(), "shared": int(count)})
        else:
            collaborators.append({"name": part, "shared": None})

    institutions.append({
        "name": name,
        "aliases": split_list(row["merged_names"], ";"),
        "country": country,
        "lat": round(lat, 5) if has_coords else None,
        "lon": round(lon, 5) if has_coords else None,
        "papers": paper_ids(row["dois"]),
        "collaborators": collaborators,
    })

# Top authors of each institution: authors whose affiliation list names the
# institution (or one of its merged names) AND who wrote at least one of the
# institution's papers. Best points first.
name_to_institution = {}
for inst in institutions:
    for alias in [inst["name"], *inst["aliases"]]:
        name_to_institution.setdefault(alias, inst)

for inst in institutions:
    inst["_candidates"] = []

for author in authors:
    author_papers = set(author["papers"])
    for affiliation in author["affiliations"]:
        inst = name_to_institution.get(affiliation)
        if inst is not None and author_papers.intersection(inst["papers"]):
            inst["_candidates"].append(author)

for inst in institutions:
    seen = set()
    best = []
    for author in sorted(inst.pop("_candidates"), key=lambda a: (-a["points"], a["name"])):
        if author["name"] in seen:
            continue
        seen.add(author["name"])
        entry = {"name": author["name"], "points": author["points"]}
        if "rank" in author:
            entry["rank"] = author["rank"]
        best.append(entry)
        if len(best) == TOP_AUTHORS_PER_INSTITUTION:
            break
    inst["topAuthors"] = best
    del inst["aliases"]


# ---------- Write the files ----------

def write_json(name, data):
    path = os.path.join(HERE, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {name} ({os.path.getsize(path) / 1e6:.1f} MB)")


write_json("papers.json", papers)
write_json("map_data.json", {"venues": venues, "institutions": institutions})
write_json("authors.json", {"rankedCount": len(ranked), "authors": authors})

located = sum(1 for i in institutions if i["lat"] is not None)
print(f"{len(papers)} papers, {len(authors)} authors ({len(ranked)} ranked), "
      f"{len(institutions)} institutions ({located} with coordinates, {filled_in} of them from extra_coordinates.csv)")
