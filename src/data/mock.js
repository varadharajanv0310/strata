/* ============================================================
   strata — mock data layer  (window.STRATA)
   Deterministic generation so figures are stable across renders.
   Wire a real pipeline here later without touching the UI.
   ============================================================ */
  // ---- seeded RNG (mulberry32) ----
  function rng(seed) {
    let t = seed >>> 0;
    return function () {
      t += 0x6d2b79f5;
      let x = Math.imul(t ^ (t >>> 15), 1 | t);
      x ^= x + Math.imul(x ^ (x >>> 7), 61 | x);
      return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hash(str) { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }

  // ---- countries ----
  const COUNTRIES = [
    { code: "IN", name: "India",          cur: "₹",  curCode: "INR", natFactor: 10.6, pppRate: 22.5, transparency: 0.29, c1: "#FF9933", c2: "#138808" },
    { code: "US", name: "United States",  cur: "$",  curCode: "USD", natFactor: 1.00, pppRate: 1.00, transparency: 0.57, c1: "#3C3B6E", c2: "#B22234" },
    { code: "GB", name: "United Kingdom", cur: "£",  curCode: "GBP", natFactor: 0.50, pppRate: 0.69, transparency: 0.49, c1: "#012169", c2: "#C8102E" },
    { code: "CA", name: "Canada",         cur: "C$", curCode: "CAD", natFactor: 0.81, pppRate: 1.20, transparency: 0.63, c1: "#D80621", c2: "#ffffff" },
    { code: "AU", name: "Australia",      cur: "A$", curCode: "AUD", natFactor: 0.93, pppRate: 1.45, transparency: 0.72, c1: "#00247D", c2: "#E4002B" },
    { code: "SG", name: "Singapore",      cur: "S$", curCode: "SGD", natFactor: 0.75, pppRate: 0.83, transparency: 0.41, c1: "#ED2939", c2: "#ffffff" },
    { code: "DE", name: "Germany",        cur: "€",  curCode: "EUR", natFactor: 0.54, pppRate: 0.75, transparency: 0.66, c1: "#000000", c2: "#DD0000" },
  ];
  const C = {}; COUNTRIES.forEach(c => C[c.code] = c);

  // ---- skill pool: durability (0-100) + long-term trend ----
  const SK = {
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
  };

  // ---- families ----
  const FAMILIES = [
    { id: "eng", name: "Engineering", hue: 230 },
    { id: "data", name: "Data & AI", hue: 265 },
    { id: "infra", name: "Infrastructure & Security", hue: 200 },
    { id: "prod", name: "Product & Design", hue: 175 },
  ];

  // ---- role definitions (usMedian in USD; demand/interest 0-100) ----
  // sk: [skillName, level]  level: A/I/B
  const L = (n, l) => [n, l];
  const ROLE_DEFS = [
    { id: "ml-eng", name: "Machine Learning Engineer", fam: "data", usMedian: 178000, demand: 94, interest: 81, growth: 0.52, demandDelta: 34,
      blurb: "Builds and ships models into production systems — the role where GenAI demand concentrates.",
      sk: [L("Python","A"),L("Machine Learning","A"),L("PyTorch","A"),L("LLM / GenAI","I"),L("System Design","I"),L("AWS","I"),L("SQL","I"),L("Vector DBs","B")],
      ladder: [["ML Engineer I",0.66],["ML Engineer II",0.86],["Senior ML Engineer",1.0],["Staff ML Engineer",1.34],["Principal ML Engineer",1.72]] },
    { id: "swe", name: "Software Engineer", fam: "eng", usMedian: 145000, demand: 88, interest: 92, growth: 0.31, demandDelta: 12,
      blurb: "The broad backbone role — general-purpose software development across the stack.",
      sk: [L("System Design","A"),L("JavaScript","A"),L("Python","I"),L("SQL","I"),L("Docker","I"),L("AWS","I"),L("Go","B")],
      ladder: [["Software Engineer I",0.62],["Software Engineer II",0.82],["Senior Engineer",1.0],["Staff Engineer",1.36],["Principal Engineer",1.78]] },
    { id: "data-eng", name: "Data Engineer", fam: "data", usMedian: 152000, demand: 90, interest: 70, growth: 0.41, demandDelta: 22,
      blurb: "Owns the pipelines and warehouses everything else is built on. High demand, lower crowding.",
      sk: [L("SQL","A"),L("Python","A"),L("dbt","I"),L("Spark","I"),L("Airflow","I"),L("Data Modeling","A"),L("AWS","I")],
      ladder: [["Data Engineer I",0.64],["Data Engineer II",0.84],["Senior Data Engineer",1.0],["Staff Data Engineer",1.32],["Principal Data Engineer",1.66]] },
    { id: "frontend", name: "Frontend Engineer", fam: "eng", usMedian: 138000, demand: 79, interest: 88, growth: 0.24, demandDelta: 6,
      blurb: "Crafts the product surface — interfaces, performance, and the design-to-code seam.",
      sk: [L("TypeScript","A"),L("React","A"),L("JavaScript","A"),L("Design Systems","I"),L("GraphQL","B"),L("System Design","I")],
      ladder: [["Frontend Engineer I",0.63],["Frontend Engineer II",0.83],["Senior Frontend Engineer",1.0],["Staff Frontend Engineer",1.30],["Principal Frontend Engineer",1.62]] },
    { id: "backend", name: "Backend Engineer", fam: "eng", usMedian: 150000, demand: 85, interest: 80, growth: 0.29, demandDelta: 10,
      blurb: "Services, APIs and data flow at scale — the load-bearing half of most products.",
      sk: [L("System Design","A"),L("Go","I"),L("Python","I"),L("SQL","A"),L("Kubernetes","I"),L("AWS","I"),L("Observability","B")],
      ladder: [["Backend Engineer I",0.63],["Backend Engineer II",0.84],["Senior Backend Engineer",1.0],["Staff Backend Engineer",1.34],["Principal Backend Engineer",1.70]] },
    { id: "data-sci", name: "Data Scientist", fam: "data", usMedian: 156000, demand: 82, interest: 90, growth: 0.27, demandDelta: 4,
      blurb: "Turns messy data into decisions — modeling, experimentation, and statistical rigor.",
      sk: [L("Python","A"),L("Statistics","A"),L("Machine Learning","I"),L("SQL","A"),L("A/B Testing","I"),L("Data Modeling","I")],
      ladder: [["Data Scientist I",0.66],["Data Scientist II",0.85],["Senior Data Scientist",1.0],["Staff Data Scientist",1.30],["Principal Data Scientist",1.60]] },
    { id: "devops", name: "DevOps Engineer", fam: "infra", usMedian: 148000, demand: 86, interest: 68, growth: 0.34, demandDelta: 16,
      blurb: "Automates the path from commit to production — pipelines, infra-as-code, reliability.",
      sk: [L("Terraform","A"),L("Kubernetes","A"),L("CI/CD","A"),L("AWS","A"),L("Docker","I"),L("Linux","I"),L("Observability","I")],
      ladder: [["DevOps Engineer I",0.64],["DevOps Engineer II",0.84],["Senior DevOps Engineer",1.0],["Staff Platform Engineer",1.33],["Principal Platform Engineer",1.66]] },
    { id: "sre", name: "Site Reliability Engineer", fam: "infra", usMedian: 162000, demand: 83, interest: 62, growth: 0.30, demandDelta: 12,
      blurb: "Keeps systems up and honest — SLOs, observability, and incident response at scale.",
      sk: [L("Kubernetes","A"),L("Observability","A"),L("Incident Response","A"),L("Linux","A"),L("Go","I"),L("Terraform","I")],
      ladder: [["SRE I",0.66],["SRE II",0.85],["Senior SRE",1.0],["Staff SRE",1.35],["Principal SRE",1.70]] },
    { id: "security", name: "Security Engineer", fam: "infra", usMedian: 158000, demand: 87, interest: 64, growth: 0.38, demandDelta: 18,
      blurb: "Defends the surface — threat modeling, app & infra security, response.",
      sk: [L("Threat Modeling","A"),L("Networking","A"),L("Incident Response","I"),L("Python","I"),L("AWS","I"),L("Linux","I")],
      ladder: [["Security Engineer I",0.65],["Security Engineer II",0.85],["Senior Security Engineer",1.0],["Staff Security Engineer",1.34],["Principal Security Engineer",1.68]] },
    { id: "cloud-arch", name: "Cloud Architect", fam: "infra", usMedian: 175000, demand: 80, interest: 58, growth: 0.33, demandDelta: 11,
      blurb: "Designs the cloud footprint — topology, cost, and resilience across regions.",
      sk: [L("AWS","A"),L("System Design","A"),L("Terraform","A"),L("Cost Optimization","I"),L("Networking","I"),L("Kubernetes","I")],
      ladder: [["Cloud Engineer",0.68],["Senior Cloud Engineer",0.86],["Cloud Architect",1.0],["Senior Cloud Architect",1.30],["Principal Architect",1.62]] },
    { id: "pm", name: "Product Manager", fam: "prod", usMedian: 160000, demand: 78, interest: 86, growth: 0.22, demandDelta: 3,
      blurb: "Owns the why and the what — strategy, roadmap, and the bridge across the org.",
      sk: [L("Roadmapping","A"),L("Stakeholder Mgmt","A"),L("A/B Testing","I"),L("SQL","B"),L("User Research","I")],
      ladder: [["Associate PM",0.62],["Product Manager",0.82],["Senior PM",1.0],["Principal PM",1.34],["Group PM",1.72]] },
    { id: "ux", name: "Product Designer", fam: "prod", usMedian: 134000, demand: 72, interest: 84, growth: 0.19, demandDelta: 1,
      blurb: "Shapes how the product feels and works — research, interaction, and systems.",
      sk: [L("Figma","A"),L("Design Systems","A"),L("User Research","A"),L("A/B Testing","B")],
      ladder: [["Product Designer I",0.64],["Product Designer II",0.83],["Senior Product Designer",1.0],["Staff Designer",1.30],["Principal Designer",1.58]] },
    { id: "mobile", name: "Mobile Engineer", fam: "eng", usMedian: 142000, demand: 74, interest: 76, growth: 0.18, demandDelta: -2,
      blurb: "Native and cross-platform apps — the role most exposed to platform shifts.",
      sk: [L("Swift","A"),L("Kotlin","A"),L("System Design","I"),L("CI/CD","I"),L("GraphQL","B")],
      ladder: [["Mobile Engineer I",0.63],["Mobile Engineer II",0.83],["Senior Mobile Engineer",1.0],["Staff Mobile Engineer",1.30],["Principal Mobile Engineer",1.60]] },
    { id: "data-analyst", name: "Data Analyst", fam: "data", usMedian: 98000, demand: 70, interest: 89, growth: 0.14, demandDelta: -4,
      blurb: "Closest-to-the-business analytics — reporting, dashboards, and decision support.",
      sk: [L("SQL","A"),L("Tableau","I"),L("Excel / Sheets","I"),L("Statistics","I"),L("Python","B")],
      ladder: [["Junior Analyst",0.66],["Data Analyst",0.84],["Senior Data Analyst",1.0],["Analytics Lead",1.28],["Head of Analytics",1.66]] },
    { id: "eng-mgr", name: "Engineering Manager", fam: "eng", usMedian: 198000, demand: 76, interest: 67, growth: 0.21, demandDelta: 5,
      blurb: "Leads the people and the delivery — the management fork off the IC ladder.",
      sk: [L("Stakeholder Mgmt","A"),L("System Design","I"),L("Roadmapping","I"),L("Observability","B")],
      ladder: [["Team Lead",0.74],["Engineering Manager",0.9],["Senior EM",1.0],["Director of Engineering",1.36],["VP Engineering",1.88]] },
    { id: "qa", name: "QA / Test Engineer", fam: "eng", usMedian: 112000, demand: 64, interest: 71, growth: 0.12, demandDelta: -6,
      blurb: "Quality and automation — increasingly merged into broader engineering roles.",
      sk: [L("CI/CD","A"),L("Python","I"),L("System Design","B"),L("JavaScript","I")],
      ladder: [["QA Engineer I",0.66],["QA Engineer II",0.84],["Senior QA Engineer",1.0],["QA Lead",1.26],["Quality Architect",1.54]] },
  ];

  const YEARS = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025];
  const FYEARS = [2026, 2027, 2028];
  const SOURCES = [
    "Aggregated public postings", "National labour survey", "Partner compensation panel",
    "Developer community survey", "Platform learner-interest signals",
  ];

  // ---- helpers ----
  function roundNice(v, code) {
    if (code === "IN") return Math.round(v / 50000) * 50000;
    if (v > 200000) return Math.round(v / 5000) * 5000;
    return Math.round(v / 1000) * 1000;
  }
  function groupIndian(n) {
    const s = String(n);
    if (s.length <= 3) return s;
    const last3 = s.slice(-3);
    let rest = s.slice(0, -3);
    rest = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",");
    return rest + "," + last3;
  }
  function fmtCur(v, code) {
    const c = C[code];
    v = Math.round(v);
    const num = code === "IN" ? groupIndian(v) : v.toLocaleString("en-US");
    return c.cur + num;
  }
  function fmtCompact(v, code) {
    const c = C[code];
    if (code === "IN") { return c.cur + (v / 100000).toFixed(v >= 1000000 ? 1 : 0) + "L"; }
    if (v >= 1000) return c.cur + (v / 1000).toFixed(v >= 100000 ? 0 : 0) + "k";
    return c.cur + Math.round(v);
  }
  function pppUSD(v, code) { return v / C[code].pppRate; }

  // ---- build per-role, per-country data ----
  const roles = ROLE_DEFS.map(def => {
    const fam = FAMILIES.find(f => f.id === def.fam);
    const countries = {};
    COUNTRIES.forEach(co => {
      const r = rng(hash(def.id + co.code));
      const median = roundNice(def.usMedian * co.natFactor, co.code);

      // salary series 2017..2025 — exponential back-cast w/ noise
      const totGrowth = def.growth * (0.7 + r() * 0.5);
      const start = median / (1 + totGrowth);
      const series = YEARS.map((y, i) => {
        const t = i / (YEARS.length - 1);
        const base = start * Math.pow(1 + totGrowth, t);
        const noise = 1 + (r() - 0.5) * 0.05;
        const dip = (y === 2020 || y === 2023) ? 0.985 : 1;
        return { year: y, value: roundNice(base * noise * dip, co.code) };
      });
      series[series.length - 1].value = median;

      // demand series 0-100
      const dCur = Math.max(20, Math.min(99, def.demand + (r() - 0.5) * 8));
      const dStart = Math.max(15, dCur - def.demandDelta - (r() - 0.5) * 6);
      const demandSeries = YEARS.map((y, i) => {
        const t = i / (YEARS.length - 1);
        const v = dStart + (dCur - dStart) * Math.pow(t, 0.9) + (r() - 0.5) * 4;
        return { year: y, value: Math.round(Math.max(10, Math.min(100, v))) };
      });
      demandSeries[demandSeries.length - 1].value = Math.round(dCur);

      // forecast demand 2026..2028 with widening band
      const slope = (dCur - dStart) / 8;
      const forecast = FYEARS.map((y, i) => {
        const step = i + 1;
        const v = Math.max(10, Math.min(100, dCur + slope * step * (0.7 + r() * 0.3)));
        const band = 4 + step * (4 + r() * 3);
        return { year: y, value: Math.round(v), lo: Math.round(Math.max(5, v - band)), hi: Math.round(Math.min(100, v + band)) };
      });

      const interest = Math.max(20, Math.min(99, def.interest + (r() - 0.5) * 8));

      // job score components (0-10)
      const payNorm = Math.min(10, (pppUSD(median, co.code) / 130000) * 7.2);
      const demandScore = dCur / 10;
      const opportunity = Math.max(0, Math.min(10, ((dCur - interest) + 50) / 10)); // demand exceeding interest => opportunity
      const total = +(0.42 * demandScore + 0.33 * payNorm + 0.25 * opportunity).toFixed(1);

      // provenance
      const sampleBase = Math.round((40 + r() * 160) * (co.code === "IN" || co.code === "US" ? 9 : co.code === "SG" ? 2.2 : 4.5));
      const conf = sampleBase > 1100 ? "high" : sampleBase > 480 ? "med" : "low";
      const kind = def.id === "data-analyst" || def.id === "qa" ? "person-level" : "job-level";

      countries[co.code] = {
        median, series, demandSeries, forecast,
        demand: Math.round(dCur), interest: Math.round(interest),
        score: { total, demand: +demandScore.toFixed(1), pay: +payNorm.toFixed(1), opp: +opportunity.toFixed(1) },
        sample: sampleBase, conf, kind,
        source: SOURCES[Math.floor(r() * SOURCES.length)],
        freshness: ["3 days", "1 week", "2 weeks", "11 days", "5 days"][Math.floor(r() * 5)],
        transparency: Math.max(0.12, Math.min(0.92, co.transparency + (r() - 0.5) * 0.18)),
      };
    });

    return {
      id: def.id, name: def.name, family: fam, blurb: def.blurb,
      skills: def.sk.map(([n, lvl]) => ({ name: n, level: lvl, dura: SK[n][0], trend: SK[n][1] })),
      ladder: def.ladder, countries,
    };
  });

  // ---- per-country rankings + percentiles for job score ----
  COUNTRIES.forEach(co => {
    const sorted = [...roles].sort((a, b) => b.countries[co.code].score.total - a.countries[co.code].score.total);
    sorted.forEach((role, i) => {
      const pct = Math.round(((i) / sorted.length) * 100);
      role.countries[co.code].score.rank = i + 1;
      role.countries[co.code].score.pctile = Math.max(1, pct);
    });
  });

  // ---- market pulse (Explore landing) ----
  function topBy(code, key) {
    return [...roles].sort((a, b) => b.countries[code][key] - a.countries[code][key]);
  }
  const marketPulse = {};
  COUNTRIES.forEach(co => {
    const rising = [...roles].sort((a, b) => {
      const ga = a.countries[co.code].demand - a.countries[co.code].demandSeries[3].value;
      const gb = b.countries[co.code].demand - b.countries[co.code].demandSeries[3].value;
      return gb - ga;
    });
    marketPulse[co.code] = {
      hottest: topBy(co.code, "demand").slice(0, 5),
      topPay: [...roles].sort((a, b) => b.countries[co.code].median - a.countries[co.code].median).slice(0, 5),
      rising: rising.slice(0, 5),
      topScore: [...roles].sort((a, b) => b.countries[co.code].score.total - a.countries[co.code].score.total).slice(0, 5),
    };
  });

  // ---- resume sample profiles ----
  const RESUME_SAMPLE = {
    name: "Parsed profile",
    title: "Senior Backend Engineer",
    years: 6,
    skills: ["Python", "Go", "Kubernetes", "AWS", "SQL", "System Design", "Observability", "Terraform"],
    matchRoles: ["backend", "devops", "sre", "cloud-arch", "ml-eng"],
    axes: [
      { axis: "Backend depth", you: 86, market: 70 },
      { axis: "Cloud / Infra", you: 78, market: 64 },
      { axis: "Data & ML", you: 41, market: 58 },
      { axis: "System design", you: 82, market: 66 },
      { axis: "Leadership", you: 55, market: 60 },
      { axis: "Frontend", you: 30, market: 52 },
    ],
  };
  const RESUME_B = {
    name: "Profile B",
    title: "Data Scientist",
    years: 5,
    skills: ["Python", "Statistics", "Machine Learning", "SQL", "PyTorch", "A/B Testing"],
    matchRoles: ["data-sci", "ml-eng", "data-analyst"],
    axes: [
      { axis: "Backend depth", you: 48, market: 70 },
      { axis: "Cloud / Infra", you: 44, market: 64 },
      { axis: "Data & ML", you: 88, market: 58 },
      { axis: "System design", you: 60, market: 66 },
      { axis: "Leadership", you: 50, market: 60 },
      { axis: "Frontend", you: 35, market: 52 },
    ],
  };

  export const STRATA = {
    COUNTRIES, C, FAMILIES, roles, YEARS, FYEARS, marketPulse,
    RESUME_SAMPLE, RESUME_B,
    fmtCur, fmtCompact, pppUSD, groupIndian,
    roleById: id => roles.find(r => r.id === id),
  };
