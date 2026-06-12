"""Representative **seed** dataset — a faithful, bit-exact Python port of the
frontend's deterministic `mock.js` generator (mulberry32 RNG + FNV-1a hash).

Because the RNG/hash and the generation order match the frontend exactly, the
API-on-seed renders byte-identically to the current app — which is how we prove
the wiring without any visual change. Every row produced here is tagged
``is_seed=True`` via the provenance layer and is fully retired when real ingested
data lands (Phase 6). **This is the only place representative data is invented**;
it is never presented as real (brief §12).
"""
from __future__ import annotations

import math

# ---------------- 32-bit RNG / hash (match JS exactly) ----------------
MASK = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    return ((a & MASK) * (b & MASK)) & MASK


def rng(seed: int):
    t = seed & MASK

    def nxt() -> float:
        nonlocal t
        t = (t + 0x6D2B79F5) & MASK
        x = _imul(t ^ (t >> 15), 1 | t)
        x = (x ^ ((x + _imul(x ^ (x >> 7), 61 | x)) & MASK)) & MASK
        return ((x ^ (x >> 14)) & MASK) / 4294967296.0

    return nxt


def hash_str(s: str) -> int:
    h = 2166136261
    for ch in s:
        h = (h ^ ord(ch)) & MASK
        h = _imul(h, 16777619)
    return h & MASK


def jround(x: float) -> int:
    """JS Math.round — half rounds toward +inf."""
    return math.floor(x + 0.5)


def jfix1(x: float) -> float:
    """Emulate +(x).toFixed(1) for our (positive) values."""
    return math.floor(x * 10 + 0.5) / 10


# ---------------- reference data (ported verbatim) ----------------
COUNTRIES = [
    {"code": "IN", "name": "India",          "cur": "₹",  "curCode": "INR", "natFactor": 10.6, "pppRate": 22.5, "transparency": 0.29, "c1": "#FF9933", "c2": "#138808"},
    {"code": "US", "name": "United States",  "cur": "$",  "curCode": "USD", "natFactor": 1.00, "pppRate": 1.00, "transparency": 0.57, "c1": "#3C3B6E", "c2": "#B22234"},
    {"code": "GB", "name": "United Kingdom", "cur": "£",  "curCode": "GBP", "natFactor": 0.50, "pppRate": 0.69, "transparency": 0.49, "c1": "#012169", "c2": "#C8102E"},
    {"code": "CA", "name": "Canada",         "cur": "C$", "curCode": "CAD", "natFactor": 0.81, "pppRate": 1.20, "transparency": 0.63, "c1": "#D80621", "c2": "#ffffff"},
    {"code": "AU", "name": "Australia",      "cur": "A$", "curCode": "AUD", "natFactor": 0.93, "pppRate": 1.45, "transparency": 0.72, "c1": "#00247D", "c2": "#E4002B"},
    {"code": "SG", "name": "Singapore",      "cur": "S$", "curCode": "SGD", "natFactor": 0.75, "pppRate": 0.83, "transparency": 0.41, "c1": "#ED2939", "c2": "#ffffff"},
    {"code": "DE", "name": "Germany",        "cur": "€",  "curCode": "EUR", "natFactor": 0.54, "pppRate": 0.75, "transparency": 0.66, "c1": "#000000", "c2": "#DD0000"},
]
C = {c["code"]: c for c in COUNTRIES}

# skill -> [durability 0-100, long-term trend]
SK = {
    "Python": [89, "rising"], "JavaScript": [73, "stable"], "TypeScript": [83, "rising"],
    "React": [71, "stable"], "SQL": [91, "stable"], "Kubernetes": [80, "rising"],
    "AWS": [85, "rising"], "Docker": [76, "stable"], "System Design": [93, "rising"],
    "Machine Learning": [87, "rising"], "PyTorch": [81, "rising"], "TensorFlow": [56, "fading"],
    "Data Modeling": [86, "stable"], "Go": [79, "rising"], "Rust": [82, "rising"],
    "Java": [63, "fading"], "Figma": [72, "stable"], "User Research": [84, "stable"],
    "Terraform": [80, "rising"], "CI/CD": [83, "stable"], "LLM / GenAI": [74, "rising"],
    "Spark": [67, "stable"], "Airflow": [65, "stable"], "Swift": [66, "stable"],
    "Kotlin": [68, "stable"], "Threat Modeling": [85, "stable"], "Roadmapping": [81, "stable"],
    "Stakeholder Mgmt": [88, "stable"], "Observability": [78, "rising"], "GraphQL": [60, "fading"],
    "Excel / Sheets": [52, "fading"], "Tableau": [58, "fading"], "dbt": [75, "rising"],
    "Statistics": [90, "stable"], "Prompt Engineering": [49, "rising"], "Linux": [82, "stable"],
    "Design Systems": [79, "rising"], "A/B Testing": [77, "stable"], "Networking": [80, "stable"],
    "Incident Response": [83, "stable"], "Cost Optimization": [76, "rising"], "Vector DBs": [62, "rising"],
}

FAMILIES = [
    {"id": "eng", "name": "Engineering", "hue": 230},
    {"id": "data", "name": "Data & AI", "hue": 265},
    {"id": "infra", "name": "Infrastructure & Security", "hue": 200},
    {"id": "prod", "name": "Product & Design", "hue": 175},
]

ROLE_DEFS = [
    {"id": "ml-eng", "name": "Machine Learning Engineer", "fam": "data", "usMedian": 178000, "demand": 94, "interest": 81, "growth": 0.52, "demandDelta": 34,
     "blurb": "Builds and ships models into production systems — the role where GenAI demand concentrates.",
     "sk": [("Python", "A"), ("Machine Learning", "A"), ("PyTorch", "A"), ("LLM / GenAI", "I"), ("System Design", "I"), ("AWS", "I"), ("SQL", "I"), ("Vector DBs", "B")],
     "ladder": [["ML Engineer I", 0.66], ["ML Engineer II", 0.86], ["Senior ML Engineer", 1.0], ["Staff ML Engineer", 1.34], ["Principal ML Engineer", 1.72]]},
    {"id": "swe", "name": "Software Engineer", "fam": "eng", "usMedian": 145000, "demand": 88, "interest": 92, "growth": 0.31, "demandDelta": 12,
     "blurb": "The broad backbone role — general-purpose software development across the stack.",
     "sk": [("System Design", "A"), ("JavaScript", "A"), ("Python", "I"), ("SQL", "I"), ("Docker", "I"), ("AWS", "I"), ("Go", "B")],
     "ladder": [["Software Engineer I", 0.62], ["Software Engineer II", 0.82], ["Senior Engineer", 1.0], ["Staff Engineer", 1.36], ["Principal Engineer", 1.78]]},
    {"id": "data-eng", "name": "Data Engineer", "fam": "data", "usMedian": 152000, "demand": 90, "interest": 70, "growth": 0.41, "demandDelta": 22,
     "blurb": "Owns the pipelines and warehouses everything else is built on. High demand, lower crowding.",
     "sk": [("SQL", "A"), ("Python", "A"), ("dbt", "I"), ("Spark", "I"), ("Airflow", "I"), ("Data Modeling", "A"), ("AWS", "I")],
     "ladder": [["Data Engineer I", 0.64], ["Data Engineer II", 0.84], ["Senior Data Engineer", 1.0], ["Staff Data Engineer", 1.32], ["Principal Data Engineer", 1.66]]},
    {"id": "frontend", "name": "Frontend Engineer", "fam": "eng", "usMedian": 138000, "demand": 79, "interest": 88, "growth": 0.24, "demandDelta": 6,
     "blurb": "Crafts the product surface — interfaces, performance, and the design-to-code seam.",
     "sk": [("TypeScript", "A"), ("React", "A"), ("JavaScript", "A"), ("Design Systems", "I"), ("GraphQL", "B"), ("System Design", "I")],
     "ladder": [["Frontend Engineer I", 0.63], ["Frontend Engineer II", 0.83], ["Senior Frontend Engineer", 1.0], ["Staff Frontend Engineer", 1.30], ["Principal Frontend Engineer", 1.62]]},
    {"id": "backend", "name": "Backend Engineer", "fam": "eng", "usMedian": 150000, "demand": 85, "interest": 80, "growth": 0.29, "demandDelta": 10,
     "blurb": "Services, APIs and data flow at scale — the load-bearing half of most products.",
     "sk": [("System Design", "A"), ("Go", "I"), ("Python", "I"), ("SQL", "A"), ("Kubernetes", "I"), ("AWS", "I"), ("Observability", "B")],
     "ladder": [["Backend Engineer I", 0.63], ["Backend Engineer II", 0.84], ["Senior Backend Engineer", 1.0], ["Staff Backend Engineer", 1.34], ["Principal Backend Engineer", 1.70]]},
    {"id": "data-sci", "name": "Data Scientist", "fam": "data", "usMedian": 156000, "demand": 82, "interest": 90, "growth": 0.27, "demandDelta": 4,
     "blurb": "Turns messy data into decisions — modeling, experimentation, and statistical rigor.",
     "sk": [("Python", "A"), ("Statistics", "A"), ("Machine Learning", "I"), ("SQL", "A"), ("A/B Testing", "I"), ("Data Modeling", "I")],
     "ladder": [["Data Scientist I", 0.66], ["Data Scientist II", 0.85], ["Senior Data Scientist", 1.0], ["Staff Data Scientist", 1.30], ["Principal Data Scientist", 1.60]]},
    {"id": "devops", "name": "DevOps Engineer", "fam": "infra", "usMedian": 148000, "demand": 86, "interest": 68, "growth": 0.34, "demandDelta": 16,
     "blurb": "Automates the path from commit to production — pipelines, infra-as-code, reliability.",
     "sk": [("Terraform", "A"), ("Kubernetes", "A"), ("CI/CD", "A"), ("AWS", "A"), ("Docker", "I"), ("Linux", "I"), ("Observability", "I")],
     "ladder": [["DevOps Engineer I", 0.64], ["DevOps Engineer II", 0.84], ["Senior DevOps Engineer", 1.0], ["Staff Platform Engineer", 1.33], ["Principal Platform Engineer", 1.66]]},
    {"id": "sre", "name": "Site Reliability Engineer", "fam": "infra", "usMedian": 162000, "demand": 83, "interest": 62, "growth": 0.30, "demandDelta": 12,
     "blurb": "Keeps systems up and honest — SLOs, observability, and incident response at scale.",
     "sk": [("Kubernetes", "A"), ("Observability", "A"), ("Incident Response", "A"), ("Linux", "A"), ("Go", "I"), ("Terraform", "I")],
     "ladder": [["SRE I", 0.66], ["SRE II", 0.85], ["Senior SRE", 1.0], ["Staff SRE", 1.35], ["Principal SRE", 1.70]]},
    {"id": "security", "name": "Security Engineer", "fam": "infra", "usMedian": 158000, "demand": 87, "interest": 64, "growth": 0.38, "demandDelta": 18,
     "blurb": "Defends the surface — threat modeling, app & infra security, response.",
     "sk": [("Threat Modeling", "A"), ("Networking", "A"), ("Incident Response", "I"), ("Python", "I"), ("AWS", "I"), ("Linux", "I")],
     "ladder": [["Security Engineer I", 0.65], ["Security Engineer II", 0.85], ["Senior Security Engineer", 1.0], ["Staff Security Engineer", 1.34], ["Principal Security Engineer", 1.68]]},
    {"id": "cloud-arch", "name": "Cloud Architect", "fam": "infra", "usMedian": 175000, "demand": 80, "interest": 58, "growth": 0.33, "demandDelta": 11,
     "blurb": "Designs the cloud footprint — topology, cost, and resilience across regions.",
     "sk": [("AWS", "A"), ("System Design", "A"), ("Terraform", "A"), ("Cost Optimization", "I"), ("Networking", "I"), ("Kubernetes", "I")],
     "ladder": [["Cloud Engineer", 0.68], ["Senior Cloud Engineer", 0.86], ["Cloud Architect", 1.0], ["Senior Cloud Architect", 1.30], ["Principal Architect", 1.62]]},
    {"id": "pm", "name": "Product Manager", "fam": "prod", "usMedian": 160000, "demand": 78, "interest": 86, "growth": 0.22, "demandDelta": 3,
     "blurb": "Owns the why and the what — strategy, roadmap, and the bridge across the org.",
     "sk": [("Roadmapping", "A"), ("Stakeholder Mgmt", "A"), ("A/B Testing", "I"), ("SQL", "B"), ("User Research", "I")],
     "ladder": [["Associate PM", 0.62], ["Product Manager", 0.82], ["Senior PM", 1.0], ["Principal PM", 1.34], ["Group PM", 1.72]]},
    {"id": "ux", "name": "Product Designer", "fam": "prod", "usMedian": 134000, "demand": 72, "interest": 84, "growth": 0.19, "demandDelta": 1,
     "blurb": "Shapes how the product feels and works — research, interaction, and systems.",
     "sk": [("Figma", "A"), ("Design Systems", "A"), ("User Research", "A"), ("A/B Testing", "B")],
     "ladder": [["Product Designer I", 0.64], ["Product Designer II", 0.83], ["Senior Product Designer", 1.0], ["Staff Designer", 1.30], ["Principal Designer", 1.58]]},
    {"id": "mobile", "name": "Mobile Engineer", "fam": "eng", "usMedian": 142000, "demand": 74, "interest": 76, "growth": 0.18, "demandDelta": -2,
     "blurb": "Native and cross-platform apps — the role most exposed to platform shifts.",
     "sk": [("Swift", "A"), ("Kotlin", "A"), ("System Design", "I"), ("CI/CD", "I"), ("GraphQL", "B")],
     "ladder": [["Mobile Engineer I", 0.63], ["Mobile Engineer II", 0.83], ["Senior Mobile Engineer", 1.0], ["Staff Mobile Engineer", 1.30], ["Principal Mobile Engineer", 1.60]]},
    {"id": "data-analyst", "name": "Data Analyst", "fam": "data", "usMedian": 98000, "demand": 70, "interest": 89, "growth": 0.14, "demandDelta": -4,
     "blurb": "Closest-to-the-business analytics — reporting, dashboards, and decision support.",
     "sk": [("SQL", "A"), ("Tableau", "I"), ("Excel / Sheets", "I"), ("Statistics", "I"), ("Python", "B")],
     "ladder": [["Junior Analyst", 0.66], ["Data Analyst", 0.84], ["Senior Data Analyst", 1.0], ["Analytics Lead", 1.28], ["Head of Analytics", 1.66]]},
    {"id": "eng-mgr", "name": "Engineering Manager", "fam": "eng", "usMedian": 198000, "demand": 76, "interest": 67, "growth": 0.21, "demandDelta": 5,
     "blurb": "Leads the people and the delivery — the management fork off the IC ladder.",
     "sk": [("Stakeholder Mgmt", "A"), ("System Design", "I"), ("Roadmapping", "I"), ("Observability", "B")],
     "ladder": [["Team Lead", 0.74], ["Engineering Manager", 0.9], ["Senior EM", 1.0], ["Director of Engineering", 1.36], ["VP Engineering", 1.88]]},
    {"id": "qa", "name": "QA / Test Engineer", "fam": "eng", "usMedian": 112000, "demand": 64, "interest": 71, "growth": 0.12, "demandDelta": -6,
     "blurb": "Quality and automation — increasingly merged into broader engineering roles.",
     "sk": [("CI/CD", "A"), ("Python", "I"), ("System Design", "B"), ("JavaScript", "I")],
     "ladder": [["QA Engineer I", 0.66], ["QA Engineer II", 0.84], ["Senior QA Engineer", 1.0], ["QA Lead", 1.26], ["Quality Architect", 1.54]]},
]

YEARS = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
FYEARS = [2026, 2027, 2028]
SOURCES = [
    "Aggregated public postings", "National labour survey", "Partner compensation panel",
    "Developer community survey", "Platform learner-interest signals",
]


def round_nice(v: float, code: str) -> int:
    if code == "IN":
        return jround(v / 50000) * 50000
    if v > 200000:
        return jround(v / 5000) * 5000
    return jround(v / 1000) * 1000


def ppp_usd(v: float, code: str) -> float:
    return v / C[code]["pppRate"]


def build_seed_dataset() -> dict:
    """Generate the full nested dataset (identical to the frontend's mock.js)."""
    roles: list[dict] = []
    for d in ROLE_DEFS:
        fam = next(f for f in FAMILIES if f["id"] == d["fam"])
        countries: dict[str, dict] = {}
        for co in COUNTRIES:
            r = rng(hash_str(d["id"] + co["code"]))
            median = round_nice(d["usMedian"] * co["natFactor"], co["code"])

            tot_growth = d["growth"] * (0.7 + r() * 0.5)
            start = median / (1 + tot_growth)
            series = []
            for i, y in enumerate(YEARS):
                t = i / (len(YEARS) - 1)
                base = start * ((1 + tot_growth) ** t)
                noise = 1 + (r() - 0.5) * 0.05
                dip = 0.985 if (y == 2020 or y == 2023) else 1
                series.append({"year": y, "value": round_nice(base * noise * dip, co["code"])})
            series[-1]["value"] = median

            d_cur = max(20, min(99, d["demand"] + (r() - 0.5) * 8))
            d_start = max(15, d_cur - d["demandDelta"] - (r() - 0.5) * 6)
            demand_series = []
            for i, y in enumerate(YEARS):
                t = i / (len(YEARS) - 1)
                v = d_start + (d_cur - d_start) * (t ** 0.9) + (r() - 0.5) * 4
                demand_series.append({"year": y, "value": jround(max(10, min(100, v)))})
            demand_series[-1]["value"] = jround(d_cur)

            slope = (d_cur - d_start) / 8
            forecast = []
            for i, y in enumerate(FYEARS):
                step = i + 1
                v = max(10, min(100, d_cur + slope * step * (0.7 + r() * 0.3)))
                band = 4 + step * (4 + r() * 3)
                forecast.append({"year": y, "value": jround(v),
                                 "lo": jround(max(5, v - band)), "hi": jround(min(100, v + band))})

            interest = max(20, min(99, d["interest"] + (r() - 0.5) * 8))

            pay_norm = min(10, (ppp_usd(median, co["code"]) / 130000) * 7.2)
            demand_score = d_cur / 10
            opportunity = max(0, min(10, ((d_cur - interest) + 50) / 10))
            total = jfix1(0.42 * demand_score + 0.33 * pay_norm + 0.25 * opportunity)

            sample_mult = 9 if co["code"] in ("IN", "US") else 2.2 if co["code"] == "SG" else 4.5
            sample_base = jround((40 + r() * 160) * sample_mult)
            conf = "high" if sample_base > 1100 else "med" if sample_base > 480 else "low"
            kind = "person-level" if d["id"] in ("data-analyst", "qa") else "job-level"
            source = SOURCES[math.floor(r() * len(SOURCES))]
            freshness = ["3 days", "1 week", "2 weeks", "11 days", "5 days"][math.floor(r() * 5)]
            transparency = max(0.12, min(0.92, co["transparency"] + (r() - 0.5) * 0.18))

            countries[co["code"]] = {
                "median": median, "series": series, "demandSeries": demand_series, "forecast": forecast,
                "demand": jround(d_cur), "interest": jround(interest),
                "score": {"total": total, "demand": jfix1(demand_score), "pay": jfix1(pay_norm), "opp": jfix1(opportunity)},
                "sample": sample_base, "conf": conf, "kind": kind,
                "source": source, "freshness": freshness, "transparency": transparency,
            }

        roles.append({
            "id": d["id"], "name": d["name"], "family": fam, "blurb": d["blurb"],
            "skills": [{"name": n, "level": lvl, "dura": SK[n][0], "trend": SK[n][1]} for (n, lvl) in d["sk"]],
            "ladder": d["ladder"], "countries": countries,
        })

    # per-country rankings + percentiles
    for co in COUNTRIES:
        sorted_roles = sorted(roles, key=lambda rr: rr["countries"][co["code"]]["score"]["total"], reverse=True)
        n = len(sorted_roles)
        for i, role in enumerate(sorted_roles):
            sc = role["countries"][co["code"]]["score"]
            sc["rank"] = i + 1
            sc["pctile"] = max(1, jround(i / n * 100))

    # market pulse (ids; the frontend reattaches role refs)
    market_pulse: dict[str, dict] = {}
    for co in COUNTRIES:
        code = co["code"]
        hottest = sorted(roles, key=lambda rr: rr["countries"][code]["demand"], reverse=True)
        top_pay = sorted(roles, key=lambda rr: rr["countries"][code]["median"], reverse=True)
        top_score = sorted(roles, key=lambda rr: rr["countries"][code]["score"]["total"], reverse=True)
        rising = sorted(roles, key=lambda rr: rr["countries"][code]["demand"] - rr["countries"][code]["demandSeries"][3]["value"], reverse=True)
        market_pulse[code] = {
            "hottest": [r["id"] for r in hottest[:5]],
            "topPay": [r["id"] for r in top_pay[:5]],
            "rising": [r["id"] for r in rising[:5]],
            "topScore": [r["id"] for r in top_score[:5]],
        }

    resume_sample = {
        "name": "Parsed profile", "title": "Senior Backend Engineer", "years": 6,
        "skills": ["Python", "Go", "Kubernetes", "AWS", "SQL", "System Design", "Observability", "Terraform"],
        "matchRoles": ["backend", "devops", "sre", "cloud-arch", "ml-eng"],
        "axes": [
            {"axis": "Backend depth", "you": 86, "market": 70},
            {"axis": "Cloud / Infra", "you": 78, "market": 64},
            {"axis": "Data & ML", "you": 41, "market": 58},
            {"axis": "System design", "you": 82, "market": 66},
            {"axis": "Leadership", "you": 55, "market": 60},
            {"axis": "Frontend", "you": 30, "market": 52},
        ],
    }
    resume_b = {
        "name": "Profile B", "title": "Data Scientist", "years": 5,
        "skills": ["Python", "Statistics", "Machine Learning", "SQL", "PyTorch", "A/B Testing"],
        "matchRoles": ["data-sci", "ml-eng", "data-analyst"],
        "axes": [
            {"axis": "Backend depth", "you": 48, "market": 70},
            {"axis": "Cloud / Infra", "you": 44, "market": 64},
            {"axis": "Data & ML", "you": 88, "market": 58},
            {"axis": "System design", "you": 60, "market": 66},
            {"axis": "Leadership", "you": 50, "market": 60},
            {"axis": "Frontend", "you": 35, "market": 52},
        ],
    }

    return {
        "countries": COUNTRIES, "families": FAMILIES, "roles": roles,
        "years": YEARS, "fyears": FYEARS, "marketPulse": market_pulse,
        "resume_sample": resume_sample, "resume_b": resume_b,
        "sources": SOURCES, "is_seed": True,
    }


if __name__ == "__main__":
    ds = build_seed_dataset()
    ml = next(r for r in ds["roles"] if r["id"] == "ml-eng")["countries"]["IN"]
    print("ML Engineer · IN  median =", ml["median"], "| score =", ml["score"])
    print("roles:", len(ds["roles"]), "| countries:", len(ds["countries"]))
