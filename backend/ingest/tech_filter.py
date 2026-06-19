"""Host-agnostic TECH-and-adjacent classifier for JobPosting titles/descriptions.

The Common Crawl corpus mines JSON-LD from generic ATS hosts (Workday, Pinpoint,
Personio) that publish postings for *every* department — retail, nursing, legal,
sales, logistics. The fusion spine only wants tech-and-adjacent roles, so we
classify each posting by TITLE first (high precision) and fall back to the
DESCRIPTION only when the title is ambiguous.

Design (deliberately simple + auditable):
  * POSITIVE  — software/web/mobile/data/ML/AI/devops/SRE/cloud/security/QA/
    network/systems/IT/hardware/electrical/embedded/firmware/technical-product/
    technical-design. Word-boundary regex so "java" doesn't match "javanese".
  * NEGATIVE  — retail/sales/nursing/clinical/driver/warehouse/food/teacher/
    finance-non-tech/admin/legal/marketing-non-technical. A negative hit on the
    TITLE vetoes a weak positive (e.g. "Sales Engineer" stays, but "Retail Sales
    Associate" / "Account Executive" / "Registered Nurse" drop).

Decision order for a title:
  1. strong-tech token present  -> TECH (even if a soft-negative also present,
     e.g. "Technical Account Manager" -> kept via 'technical').
  2. hard-negative token present -> NOT TECH.
  3. soft/general tech token present -> TECH.
  4. else -> fall back to description: strong-tech token present -> TECH.
  5. else -> NOT TECH.
"""
from __future__ import annotations

import re

# --- STRONG tech signals: presence almost always means a tech-and-adjacent role.
_STRONG = [
    r"software", r"\bswe\b", r"\bsde\b", r"developer", r"\bdev\b", r"programmer",
    r"\bfront[\s-]?end\b", r"\bback[\s-]?end\b", r"full[\s-]?stack",
    r"\bweb\b", r"\bmobile\b", r"\bios\b", r"\bandroid\b",
    r"\bdata\s+(engineer|scientist|analyst|architect)", r"\bdata\b.*\b(engineer|scien|analy|platform|pipeline)",
    r"machine\s+learning", r"\bml\b", r"\bml[\s-]?ops\b", r"\bai\b", r"\bartificial\s+intelligence\b",
    r"deep\s+learning", r"\bnlp\b", r"\bllm\b", r"computer\s+vision", r"\bgen[\s-]?ai\b",
    r"dev[\s-]?ops", r"\bsre\b", r"site\s+reliability", r"platform\s+engineer",
    r"\bcloud\b", r"\baws\b", r"\bazure\b", r"\bgcp\b", r"kubernetes", r"\bk8s\b",
    r"infrastructure\s+engineer", r"systems?\s+engineer", r"\bsysadmin\b",
    r"security\s+engineer", r"cyber[\s-]?security", r"\binfosec\b", r"\bappsec\b",
    r"penetration\s+test", r"\bpentest\b", r"security\s+analyst",
    r"\bqa\b", r"\bsdet\b", r"quality\s+(engineer|assurance)", r"test\s+(engineer|automation)",
    r"\bnetwork(ing)?\s+engineer", r"network\s+administrat",
    r"\bembedded\b", r"firmware", r"\bfpga\b", r"\basic\b", r"\bvlsi\b",
    r"\belectrical\s+engineer", r"\belectronics?\s+engineer", r"hardware\s+engineer",
    r"\bdatabase\s+(engineer|administrat)", r"\bdba\b",
    r"solutions?\s+(engineer|architect)", r"sales\s+engineer", r"forward[\s-]?deployed",
    r"technical\s+(account|program|product|project|lead|writer|support|consultant|architect|specialist)",
    r"\bprompt\s+engineer", r"\bblockchain\b", r"\bweb3\b", r"\bsmart\s+contract\b",
    r"\bgame\s+(developer|engineer|programmer)", r"\bgraphics\s+engineer\b",
    r"\bsystems?\s+administrator\b", r"\bit\s+(support|engineer|administrator|technician|specialist|analyst|manager|operations)",
    r"\bhelp\s?desk\b", r"\bdesktop\s+support\b", r"\btech(nology)?\s+(lead|manager|director)\b",
    r"\brobotics?\b", r"\bautomation\s+engineer\b", r"\bcontrols?\s+engineer\b",
    r"\bmechatronics?\b", r"\bbioinformatics?\b", r"\bgis\b\s|\bgis\s+(analyst|developer|specialist)",
    r"\bux\b", r"\bui\b", r"user\s+experience", r"product\s+design", r"\bux/?ui\b",
    r"design\s+system", r"interaction\s+design",
    r"\bscrum\s+master\b", r"\bagile\s+coach\b",
    r"engineering\s+(manager|director|lead)", r"\bvp\s+(of\s+)?engineering\b",
    r"head\s+of\s+(engineering|data|product|design|platform|security|infrastructure)",
    r"director\s+of\s+(engineering|data|product|design|platform|security|technology)",
    r"\bproduct\s+(owner|lead|director)\b",
]

# --- soft/general tech tokens: count only if no hard-negative on the title.
_SOFT = [
    r"\bengineer\b", r"\barchitect\b", r"\banalyst\b", r"\bproduct\s+manager\b",
    r"\btechnical\b", r"\btechnolog", r"\bdigital\b", r"\bsystems?\b", r"\bplatform\b",
    r"\bdesigner\b", r"\bresearch\s+scientist\b", r"\bresearcher\b",
    r"\bintegration\b", r"\bapi\b", r"\bsaas\b", r"\bcomputing\b",
]

# --- HARD negatives: a title hit vetoes everything except a STRONG hit.
_NEGATIVE = [
    r"\bnurse\b", r"\bnursing\b", r"\brn\b", r"\blpn\b", r"\bcna\b", r"clinical",
    r"\bphysician\b", r"\bdoctor\b", r"\bsurgeon\b", r"\bpharmac", r"\bdentist\b",
    r"\bcaregiver\b", r"\btherapist\b", r"\bmedical\s+assistant\b", r"\bphlebotom",
    r"\bpatient\b", r"\bhealthcare\s+(assistant|aide)", r"\bveterinar",
    r"\bretail\b", r"\bcashier\b", r"\bsales\s+associate\b", r"\bstore\s+(manager|associate)",
    r"\bmerchandis", r"\bstock\b", r"\bwarehouse\b", r"\bforklift\b", r"\bpicker\b",
    r"\bdriver\b", r"\bdelivery\b", r"\bchauffeur\b", r"\bcourier\b", r"\btrucking\b",
    r"\blogistics\b", r"\bsupply\s+chain\b", r"\bprocurement\b",
    r"\bcook\b", r"\bchef\b", r"\bbarista\b", r"\bserver\b\s|\bwaiter\b", r"\bwaitress\b",
    r"\bkitchen\b", r"\bfood\s+(service|prep)", r"\brestaurant\b", r"\bhospitality\b",
    r"\bteacher\b", r"\bteaching\b", r"\btutor\b", r"\bprofessor\b", r"\blecturer\b",
    r"\beducator\b", r"\bfaculty\b", r"\bcoach\b\s|\bbabysitter\b", r"\bchildcare\b",
    r"\baccount\s+(executive|manager|director)\b", r"\bsales\s+(manager|director|representative|rep|consultant|specialist|executive|advisor|agent|lead|development)\b",
    r"\bsales\s+development\s+representative\b", r"\binstitutional\s+sales\b",
    r"\bbusiness\s+development\b", r"\bsdr\b", r"\bbdr\b", r"\binside\s+sales\b",
    r"\bmarketing\b", r"\bbrand\b", r"\bsocial\s+media\b", r"\bcopywriter\b",
    r"\bcontent\s+(writer|creator|strategist)\b", r"\bseo\b\s", r"\bpublic\s+relations\b",
    r"\bcommunications?\s+(manager|specialist|coordinator)\b", r"\bjournalist\b", r"\breporter\b",
    r"\blawyer\b", r"\battorney\b", r"\bcounsel\b", r"\blegal\b", r"\bparalegal\b",
    r"\bcompliance\b", r"\bregulatory\b", r"\baccountant\b", r"\baccounting\b",
    r"\bbookkeep", r"\bauditor\b", r"\btax\b", r"\bpayroll\b", r"\bfinanc(e|ial)\s+(analyst|advisor|manager|controller|planner)\b",
    r"\bunderwrit", r"\bactuary\b", r"\bteller\b", r"\bbanker\b", r"\binvestment\s+banking\b",
    r"\brecruit", r"\btalent\s+acquisition\b", r"\bhuman\s+resources?\b", r"\bhr\s+(manager|generalist|business)\b",
    r"\badministrative\s+assistant\b", r"\breceptionist\b", r"\bexecutive\s+assistant\b",
    r"\boffice\s+(manager|administrator)\b", r"\bsecretary\b", r"\bclerk\b", r"\bdata\s+entry\b",
    r"\bcustomer\s+service\b", r"\bcall\s+center\b", r"\bcustomer\s+success\b", r"\bcustomer\s+support\b",
    r"\bcleaner\b", r"\bjanitor\b", r"\bhousekeep", r"\bsecurity\s+guard\b", r"\bsecurity\s+officer\b",
    r"\bconstruction\b", r"\bplumber\b", r"\bcarpenter\b", r"\bwelder\b", r"\bpainter\b",
    r"\blandscap", r"\bmaintenance\s+(technician|worker)\b", r"\bmechanic\b",
    r"\bsales\b.*\bassociate\b", r"\breal\s+estate\b", r"\binsurance\s+agent\b",
    r"\bpsycholog", r"\bsocial\s+worker\b", r"\bcounsel(or|ing)\b",
    r"\bphotograph", r"\bvideograph", r"\bgraphic\s+design", r"\billustrator\b",
]

_RE_STRONG = re.compile("|".join(_STRONG), re.IGNORECASE)
_RE_SOFT = re.compile("|".join(_SOFT), re.IGNORECASE)
_RE_NEG = re.compile("|".join(_NEGATIVE), re.IGNORECASE)

# --- VETO: titles that must NEVER be tech, even when a strong token fires (checked
# FIRST). Catches gig/crowdwork ("AI Trainer", data-annotation) that ride the bare
# "ai"/"data scientist" tokens, and retail ("Sales & Service Consultant") that rides
# a techy description. Found leaking into clusters on the bounded ATS proof (2026-06).
_VETO = [
    r"\bai\s+trainer\b", r"\bai\s+tutor\b", r"\bsearch\s+quality\s+rater\b",
    r"sales\s*&\s*service\s+consultant", r"sales\s+and\s+service\s+consultant",
    r"\bdata\s+annotat", r"\bbrand\s+ambassador\b", r"\bcrowd\s*work\b",
]
_RE_VETO = re.compile("|".join(_VETO), re.IGNORECASE)


def classify(title: str | None, description: str | None = None) -> bool:
    """True if the posting is tech-and-adjacent. See module docstring for order."""
    t = (title or "").strip()
    if t and _RE_VETO.search(t):
        return False                   # gig/crowdwork/retail — never tech
    if t:
        strong_t = bool(_RE_STRONG.search(t))
        neg_t = bool(_RE_NEG.search(t))
        if strong_t:
            return True            # strong tech wins, even over a soft negative
        if neg_t:
            return False           # hard negative vetoes weak signals
        if _RE_SOFT.search(t):
            return True            # generic-tech (Engineer/Architect/Analyst...) w/o negative
    # ambiguous title -> description fallback (strong signal only, to stay precise)
    d = (description or "")[:1500]
    if d and _RE_STRONG.search(d) and not _RE_NEG.search(t or ""):
        return True
    return False


def tech_share(titles) -> float:
    """Convenience: fraction of an iterable of titles classified tech."""
    titles = list(titles)
    if not titles:
        return 0.0
    return sum(classify(t) for t in titles) / len(titles)
