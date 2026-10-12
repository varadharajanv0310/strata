# Ecosystem build spec

One product for students in college who want tech careers. It is built from two repositories:
**strata** (market data) and **InternshipOS** (accounts and everything personal).

How to read this document:
- Items without a marker were decided by the owner.
- Items marked **(default)** are working assumptions; the owner can overturn them at any time.
- Paid/free and placement decisions are deferred by the owner; until then the defaults below apply.

Every change made while building this is recorded in [`BUILD_LEDGER.md`](BUILD_LEDGER.md): what,
why, where it lives and how to remove it.

Last updated: 12 October 2026.

---

## 1. Product

- **Audience:** students in college aiming for tech roles; India-first **(default)**.
- **Journey:** understand the market → understand yourself → get a plan → build proof → land internships.
- **Business model:** all data is free; all personalization is paid, because it costs money to run.
- **The quiz is the paid entry.** A free account unlocks the free site (needed so the tracker can save
  anything); paying starts the quiz, and the quiz unlocks personalization **(default flow)**.
- **Outside the product:** campus placements and interview prep, anything after college, community or
  crowdsourced content, colleges or employers as customers, and generic content libraries.

---

## 2. Architecture: one product, two repositories

| Repository | Path | Responsibility |
|---|---|---|
| strata | `D:\A1- Job market` | Market data, the public role explorer, the role catalog (source of truth), and shared per-role content: trait profiles, roadmap templates and resource lists |
| InternshipOS | `D:\Internship tracker Chat gpt` | Accounts, profiles, résumé vault, internship finder and matching, tracker, dashboard, calendar, quiz, payments, AI layer, digests |

- **One domain, one login (default).** Both apps are served under one domain using path routing,
  sharing one session cookie, so the user experiences a single product.
- **Contract between the repos (default).** strata exposes a read-only API: the role catalog (skills,
  pay progression, trajectory), per-role trait profiles and per-role roadmap templates. InternshipOS
  syncs and caches what it needs.
- **Shared role vocabulary.** Every internship in InternshipOS is classified onto a strata role id.
  Matching, insights and roadmaps depend on this.
- **strata endpoints InternshipOS calls (built):**
  - `POST /api/roles/classify` with `{"titles": [...up to 500], "alternatives": 2}` returns, per
    title in order: `role_id`, `name`, `family`, `confidence` (`high` / `med` / `low`, or `none` for a
    blank title), `score`, `via` (the surface that matched) and runner-up `alternatives`. Treat `low`
    as unclassified. Posting noise ("Intern", seasons, years, "Werkstudent", "(m/w/d)", location
    suffixes) is stripped by strata.
  - `GET /api/role-content/dimensions`, `GET /api/role-content/traits` (every role's 11 trait scores)
    and `GET /api/role-content/{role_id}` (one role's trait profile and roadmap template).
  - `GET /api/roles/{role_id}` (skills, pay progression, trajectory).
- **AI layer.** Reuse InternshipOS's provider boundary, usage ledger and grounded-fact validator. The model is
  configuration (DeepSeek Flash connected on 11 October 2026). Every model call first checks the user's
  paid entitlement and per-user cost ceiling.
- **Working rule for InternshipOS:** it is actively developed on `main`. Ecosystem work happens in a
  separate git worktree and branch, never in the owner's working copy.

---

## 3. Scope

### Free (default placement in brackets)
| Feature | Notes |
|---|---|
| Market explorer [strata] | Roles, salaries, demand, skills across 7 countries |
| Career path simulator [strata] | Where a role leads over 5–10 years, pay at each step |
| Internship finder + matching [InternshipOS] | The existing engine, made multi-user |
| Application tracker [InternshipOS] | Every application and its stage |
| Application dashboard & analytics [InternshipOS] | See §4.8 |
| Résumé vault + one-time parser check [InternshipOS] (default: free) | See §4.7 |
| Résumé-to-role match [InternshipOS] | How ready their résumé is for a role |
| Season calendar [InternshipOS] (default: free) | When internship programs open |
| Calendar & tasks, base [InternshipOS] | Deadlines and their own tasks. Paid content appears here for paid users |
| Daily & weekly digest, basic (default) | New matches and deadlines. The paid tier adds tasks and goals |
| Portfolio site generator (default: free) | Site built from their profile and projects |
| Coding-profile import (default: free) | LeetCode / Codeforces stats |

### Paid
| Feature | Notes |
|---|---|
| Quiz → profile | The paid entry; see §4.1 |
| Per-role insights column on every role page | See §4.2 |
| Roadmaps (the learning platform) | See §4.3 |
| Weekly goals, seasonal goals | See §4.5 |
| Generated next actions (default: paid) | Manual tasks stay free |
| Application help | See §4.6 |
| Project help | See §4.9 |
| Reviews | See §4.10 |
| Ask-anything, scoped (default: paid, low priority) | Answers drawn from their profile and strata data |

### Removed (do not re-add)
Reusable answer bank; per-application résumé-parser check (moved to the vault, once); outreach beyond the
first cold email (contact finding, follow-up sequences, LinkedIn notes, referral requests, thank-you notes);
analytics on "skills your matches ask for most"; role-insight items for time-to-internship-ready, how
people get in, and readiness against graded skills; the roadmap's "internship-ready" line; practice
exercises, mini-builds, self-checks and demand-linked learning in the learning platform; owner-only
InternshipOS features in the mass product (Gmail, auto-apply, the owner's monthly budget).

### Deferred
Making strata ready to go public (paginating the 11 MB payload, scoping the publish gate to measured
data); pricing; WhatsApp alerts; mobile app.

---

## 4. Feature details

### 4.1 Quiz (paid entry) (proposed design)
- Measures only what changes a recommendation:
  - **interests:** building, analyzing, designing, running systems, working with people
  - **work style:** comfort with ambiguity, on-call and firefighting, solo focus vs collaboration,
    research vs shipping, customer contact
  - **constraints:** location, timeline, pay expectations, weekly hours available
- Uses scenario questions, not self-ratings.
- Pre-fills everything known from the résumé, GitHub and profile, and asks only the rest. Takes a few minutes.
- Produces a profile in the same dimensions as each role's trait profile (§5), so matching is measurable.
- Can be retaken at any time. Saves, applications and skips keep refining it.

### 4.2 Per-role insights column (every role page, paid)
- **Rating for every role, computed without a model call.** Inputs: interests and work style (quiz vs the role's trait profile), skills (verified from résumé
  and GitHub), and market value (strata demand, pay, progression). Users can sort and filter all roles
  by their own rating.
- **Written insight:** generated the first time they open a role, then cached for that user, role and profile
  version. It covers:
  - why the role suits them, traced to their actual quiz answers
  - honest friction ("a third of this role is on-call; you said interruptions drain you")
  - their head start: which skills and projects transfer, and how to frame them
  - where the role leads for them, with pay from strata
- **Their top roles side by side.**
- **Grounding:** only profile facts and strata data, enforced by the validator.

### 4.3 Roadmaps: the learning platform (paid)
- **Scope:** one target role or a selected set. A set merges the skills the roles share into one
  path, and each step is tagged with the roles it serves.
- **Order:** built from strata's skill grades (core, then important, then nice-to-have). Skips
  what they've already proven. Lasting and rising skills come before fading ones.
- **Each step:** what to learn, which tools, where to start, resource links (free first, paid options
  labeled) and a time estimate.
- **Each phase ends with something built,** which feeds project help and the résumé.
- **Timing:** planned backward from when applications open and the student's weekly hours; re-plans
  automatically when they fall behind or move ahead.
- **Primary and backup role,** with shared skills scheduled first.
- **Progressive UI:** a roadmap map; they advance as they complete steps.
- **No teaching and no tests.** Resource links are generated, so every link is checked to resolve before
  it is shown, and rechecked on a schedule.

### 4.4 Calendar (the hub)
- Base: InternshipOS's existing Calendar & tasks page and its "Your next actions" panel.
- Shows application deadlines and their own tasks (free), plus roadmap steps and weekly/seasonal goals
  (paid).
- **External sync (default):** an ICS subscription link that works with Google, Apple and Outlook and
  needs no Google OAuth verification. Google Calendar OAuth sync stays in the owner's personal build.

### 4.5 Weekly and seasonal goals (paid)
- **Weekly plan:** generated, not hand-written. It balances learning, building and applying within their
  weekly hours (e.g. finish a roadmap step, ship a project milestone, apply to 5). Unfinished items carry
  over, with a weekly review of done vs slipped.
- **Seasonal goals:** follow the internship calendar (build off-season, apply on-season). Before each season opens there's a readiness target:
  résumé versions that parse cleanly, 2–3 strong projects, pinned repos, LinkedIn updated, a target list.
  A season readiness score tracks progress toward it.

### 4.6 Application help (paid)
- **Per-application pack:** a tailored résumé version, answers to that application's questions, which
  projects to lead with, and the 3 points to stress. Approved facts only.
- **Requirement breakdown:** met, partly met or missing, with honest handling of partial matches.
- **Knockout check:** eligibility rules or screening questions they would fail, flagged before they apply.
- **Résumé versions by role family,** with a recommendation of which to send.
- **Effort estimate per application:** quick apply vs assignment vs long form.
- **Cold email draft:** a single, grounded first email.
- **Company brief before applying:** what the company builds, its stack, what interns work on.
- **Application strategy:** what to apply to first, weighing fit, worth, deadline and effort.
- **Rejection patterns:** what several rejections have in common, and what to change.

### 4.7 Résumé vault
- Upload once → a parser check showing how applicant-tracking software reads it (columns, tables,
  icons, headers that break parsing). Runs once per uploaded version, never per application.
- Résumé-to-role match (free).

### 4.8 Dashboard & analytics (free)
- **Funnel:** found → saved → applied → test/interview → offer, with conversion at each step.
- **Response rate** by role, company type, source and résumé version.
- **Responsiveness:** how many applications got no response, and the time to first response.
- **Pace:** applications per week against their goal.
- **Fit:** how well what they apply to matches them.
- **The paywall line:** the numbers are free; "here's what to change" is paid.

### 4.9 Project help (paid)
- **Project plan:** scope, stack, milestones, and what "done" means for a résumé (deployed, README, real
  numbers).
- **Steering:** away from clichéd projects, toward ones that stand out for the target role.
- **Review** at each milestone.
- **Résumé bullets** written from the actual repository.
- **Selection:** which projects to lead with, merge or drop for each target role.

### 4.10 Reviews (paid)
- **Covers:** résumé, GitHub, LinkedIn, portfolio, project code.
- **Output:** fixes ranked by importance, not a score.
- **Re-review** after changes shows what improved.

### 4.11 Daily and weekly digest
- **Daily** (about 30 seconds to read; skipped if there's nothing new):
  - top ~3 new matches, each with a one-line reason
  - saved internships closing within 48 hours
  - up to 3 tasks for today (paid)
  - what's waiting on them: unanswered application questions, profile gaps
  - one-tap actions (done, snooze, save, not interested) that also feed matching
- **Weekly:**
  - done vs slipped, with automatic re-planning (paid)
  - next week's plan (paid)
  - application pulse: applied vs goal, responses, no response for 2+ weeks
  - good matches that expired before they applied
  - profile gaps that block matching
  - market changes for their target role, only when something moved
- **Channels (default):** in-app and email; WhatsApp later.

---

## 5. Shared per-role content (generated in strata)

Generated once per role and shared across users, so it is cheap to serve. It is stamped `generated`,
following the catalog's rules.

- **Trait profile:** the same dimensions as the quiz (§4.1), so a quiz result and a role can be compared.
- **Roadmap template:** ordered steps per role, built from the role's graded skills, tools, time
  estimates, and resource links (free + paid) that are checked before use.
- Per-user personalization is assembled from these templates plus the user's profile. Per-user model
  calls are kept to the written narrative.

---

## 6. Platform

- **Accounts:** multi-user accounts, per-user data isolation, and free/paid entitlements.
- **Payments:** test mode first. The provider account and keys are the owner's (likely Razorpay).
- **AI cost control:** per-user metering and cost ceilings.
- **Privacy center:** consent for quiz and personality data, data export and deletion (DPDP Act).
- **Verified profile facts:** résumé parsing, GitHub sign-in.
- **Notifications:** in-app and email.

---

## 7. Build phases

1. **Foundation** (no UI decisions needed)
   - a. strata role-catalog API; an InternshipOS classifier that maps internships onto strata role ids.
   - b. Per-role generated content in strata: trait profiles, roadmap templates, resource lists with
     link checking.
2. **Multi-user core** (InternshipOS): accounts, isolation, entitlements; free features moved onto
   accounts (finder, tracker, dashboard, vault + parser check, résumé-to-role match).
3. **Paid entry:** payments (test mode) and the quiz → profile.
4. **Personalization:** the per-role insights column, the roadmap UI, calendar integration, goals, digests.
5. **Application help, project help, reviews.**
6. **Later:** strata public-readiness, pricing, WhatsApp, mobile.

---

## 8. Needs the owner

- Pricing (monthly subscription vs a pass per placement season).
- A payments provider account and its keys.
- An AI provider key in the deployment environment (the model is chosen through InternshipOS's existing benchmark).
- Confirm or overturn every **(default)** in this document.

## 9. Rules carried over

- Generated content is stamped `generated`; measured data upgrades it.
- Paid outputs never invent facts: the grounded validator enforces this.
- strata's market data stays roles-only. Companies live in InternshipOS.
- Commits stay local; nothing is pushed without explicit authorization.
