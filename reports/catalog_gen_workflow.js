export const meta = {
  name: 'strata-generate-catalog',
  description: 'Generate the strata tech-role catalog (≈350 roles × 7 countries) from model knowledge — the catalog IS the product',
  phases: [
    { title: 'Generate roles' },
    { title: 'Generate skill vocab' },
  ],
}

// ---------------------------------------------------------------------------
// The catalog is GENERATED from the model's knowledge of the tech job market.
// No datasets, no taxonomies, no corpus. Each role carries per-country salary
// medians (LOCAL currency, real market reality), skills, seniority ladder, and a
// description. Everything is stamped 'generated' downstream — the honesty stamp.
// ---------------------------------------------------------------------------

const COUNTRIES = 'IN (India, INR ₹, annual), US (USD $), GB (UK, GBP £), CA (Canada, CAD C$), AU (Australia, AUD A$), SG (Singapore, SGD S$), DE (Germany, EUR €)'

// Calibration anchors so 18 independent agents produce consistent salary scales.
const SALARY_ANCHOR = `Salary CALIBRATION — a MID-LEVEL (3-5 yr) Software Engineer earns roughly:
IN ₹1,800,000 (18 LPA), US $150,000, GB £70,000, CA C$120,000, AU A$135,000, SG S$110,000, DE €75,000.
Scale every other role's MEDIAN realistically off these anchors per country (a Principal/Staff role ~1.6-2x a mid SWE; a support/QA/junior-heavy role ~0.5-0.8x; ML/AI/Security/Cloud specialists carry a premium; India keeps a larger discount for support/ops roles and a smaller one for elite AI/quant roles). Give the MEDIAN full-time annual total comp in LOCAL currency as an integer (India in absolute INR, e.g. 1800000 — NOT lakhs).`

// The 16 canonical ids measured data already keys on — use these EXACT ids so real
// facts upgrade the generated cell. Everything else: a fresh kebab-case id.
const CANONICAL = `Use these EXACT ids for these roles (measured data upgrades them): ml-eng=Machine Learning Engineer, swe=Software Engineer, data-eng=Data Engineer, frontend=Frontend Engineer, backend=Backend Engineer, data-sci=Data Scientist, devops=DevOps Engineer, sre=Site Reliability Engineer, security=Security Engineer, cloud-arch=Cloud Architect, pm=Product Manager, ux=UX Designer, mobile=Mobile Engineer, data-analyst=Data Analyst, eng-mgr=Engineering Manager, qa=QA Engineer.`

const ROLE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    roles: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          id: { type: 'string', description: 'stable kebab-case id' },
          name: { type: 'string' },
          family: { type: 'string', enum: ['eng', 'data', 'infra', 'prod'] },
          blurb: { type: 'string', description: '1-2 sentences: what the work is actually like' },
          skills: {
            type: 'array', minItems: 5, maxItems: 11,
            items: {
              type: 'object', additionalProperties: false,
              properties: {
                name: { type: 'string' },
                importance: { type: 'string', enum: ['A', 'I', 'B'] },
              },
              required: ['name', 'importance'],
            },
          },
          ladder: {
            type: 'array', minItems: 3, maxItems: 6,
            items: {
              type: 'object', additionalProperties: false,
              properties: {
                title: { type: 'string' },
                mult: { type: 'number', description: 'pay multiple vs Senior=1.0 (junior<1, staff/principal>1)' },
              },
              required: ['title', 'mult'],
            },
          },
          demand: { type: 'integer', description: 'current market demand 20-99' },
          demandDelta: { type: 'integer', description: 'demand growth in points over ~8y window' },
          interest: { type: 'integer', description: 'learner/candidate interest 20-99' },
          growth: { type: 'number', description: 'salary growth factor 0.0-1.0 over the window' },
          salary: {
            type: 'object', additionalProperties: false,
            properties: {
              IN: { type: 'integer' }, US: { type: 'integer' }, GB: { type: 'integer' },
              CA: { type: 'integer' }, AU: { type: 'integer' }, SG: { type: 'integer' }, DE: { type: 'integer' },
            },
            required: ['IN', 'US', 'GB', 'CA', 'AU', 'SG', 'DE'],
          },
        },
        required: ['id', 'name', 'family', 'blurb', 'skills', 'ladder', 'demand', 'demandDelta', 'interest', 'growth', 'salary'],
      },
    },
  },
  required: ['roles'],
}

const SLICES = [
  { key: 'core-swe', fam: 'eng', target: 20, brief: 'Core software engineering: general SWE, backend, frontend, full-stack, web, API, platform-product engineers.' },
  { key: 'lang-specialists', fam: 'eng', target: 20, brief: 'Language/runtime specialist engineers: Java, Python, Go, Rust, C#/.NET, C++, Ruby/Rails, PHP/Laravel, Node.js, Scala, Elixir, Kotlin backend.' },
  { key: 'mobile', fam: 'eng', target: 18, brief: 'Mobile & client engineering: iOS, Android, React Native, Flutter, mobile architect, mobile game, cross-platform, Unity/Unreal client.' },
  { key: 'data-eng', fam: 'data', target: 22, brief: 'Data & analytics engineering: data engineer, analytics engineer, ETL/ELT, data platform, streaming/Kafka, warehouse (Snowflake/Databricks), dbt, data infra.' },
  { key: 'data-sci', fam: 'data', target: 22, brief: 'Data science & statistics: data scientist, applied scientist, research scientist, statistician, econometrician, quantitative analyst/researcher, experimentation scientist, decision scientist, actuary.' },
  { key: 'ml-ai', fam: 'data', target: 24, brief: 'ML & AI engineering: ML engineer, MLOps, AI engineer, computer vision, NLP, deep learning, recommender systems, search/ranking, ML infra/platform, ML research eng.' },
  { key: 'genai-llm', fam: 'data', target: 22, brief: 'Emerging GenAI/LLM roles: LLM engineer, prompt engineer, GenAI application engineer, RAG engineer, AI agent engineer, forward-deployed engineer, AI product engineer, AI safety/alignment, evaluation engineer, AI red-teamer.' },
  { key: 'data-analysis-bi', fam: 'data', target: 18, brief: 'Data analysis & BI: data analyst, BI analyst/developer, product analyst, marketing/growth analyst, business analyst, financial analyst (tech), reporting analyst, analytics manager.' },
  { key: 'devops-sre', fam: 'infra', target: 22, brief: 'DevOps / SRE / Platform: DevOps engineer, SRE, platform engineer, build/release engineer, CI/CD engineer, observability engineer, infrastructure engineer, developer-experience/internal-tools engineer.' },
  { key: 'cloud-infra', fam: 'infra', target: 20, brief: 'Cloud & infrastructure architecture: cloud architect/engineer (AWS/Azure/GCP), solutions architect, systems architect, infrastructure architect, FinOps engineer, virtualization/VMware, storage engineer.' },
  { key: 'security', fam: 'infra', target: 24, brief: 'Security: security engineer, application/product security (AppSec), penetration tester, offensive security/red team, blue team/SOC analyst, GRC/compliance, cloud security, DFIR/forensics, security architect, IAM engineer, malware analyst, threat intel.' },
  { key: 'networking-it', fam: 'infra', target: 18, brief: 'Networking, systems & IT: network engineer/architect, systems administrator, IT support/help desk, telecom/VoIP engineer, wireless/RF, datacenter technician, endpoint/desktop engineer.' },
  { key: 'qa-test', fam: 'eng', target: 16, brief: 'QA & testing: QA engineer, SDET, test automation engineer, performance/load test engineer, manual QA, QA lead, quality/release manager, game QA.' },
  { key: 'product', fam: 'prod', target: 20, brief: 'Product management: product manager, technical PM, group/principal PM, product owner, growth PM, platform PM, data/AI PM, product operations, associate PM.' },
  { key: 'design-ux', fam: 'prod', target: 20, brief: 'Design & UX: product designer, UX designer, UI designer, UX researcher, design systems designer, interaction designer, service designer, content designer/UX writer, design manager, motion/visual designer.' },
  { key: 'eng-leadership', fam: 'eng', target: 16, brief: 'Engineering leadership & staff+: engineering manager, senior/director/VP engineering, tech lead, staff/principal/distinguished engineer, CTO, head of platform, technical program lead.' },
  { key: 'specialized', fam: 'eng', target: 24, brief: 'Specialized/domain engineering: embedded, firmware, robotics, game engine/graphics, blockchain/web3/smart-contract, AR/VR/XR, quantum, bioinformatics, hardware/FPGA, DBA, ERP/SAP, Salesforce, ServiceNow, low-code/RPA, GIS, simulation/CAE, audio/DSP.' },
  { key: 'adjacent-ops', fam: 'prod', target: 20, brief: 'Tech-adjacent & delivery: developer advocate/DevRel, technical writer, sales engineer, solutions engineer, support engineer, implementation/onboarding engineer, scrum master, agile coach, technical program manager, release manager, data governance, ML data/labeling lead, technical recruiter (tech).' },
]

phase('Generate roles')
const batches = await parallel(SLICES.map(s => () =>
  agent(
    `You are generating part of strata's tech-role catalog from your own knowledge of the global tech job market. This is the PRODUCT — accurate, real roles a person could search for, not a sample.\n\n` +
    `SLICE: ${s.brief}\nGenerate ${s.target} DISTINCT real roles in THIS slice only (do not stray into other slices). Prefer family '${s.fam}' but use the correct family per role (eng | data | infra | prod).\n\n` +
    `${CANONICAL}\n\n${SALARY_ANCHOR}\n\n` +
    `Countries (give a salary MEDIAN for each): ${COUNTRIES}.\n` +
    `For each role give: id, name, family, blurb (what the work is like), 6-10 skills each tagged A (core/defines the role) / I (important) / B (nice-to-have), a 4-5 rung seniority ladder (title + pay multiple vs Senior=1.0), demand (20-99), demandDelta, interest (20-99), growth (0-1), and the 7 per-country salary medians in LOCAL currency.\n` +
    `Be accurate and realistic — these numbers should match what a person finds googling the role. Return ONLY the JSON.`,
    { label: `gen:${s.key}`, phase: 'Generate roles', schema: ROLE_SCHEMA }
  ).then(r => (r && r.roles) ? r.roles : [])
))

// ---- dedup + normalize (plain JS, no agent) ----
const clampi = (v, lo, hi, d) => { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : d }
const CC = ['IN', 'US', 'GB', 'CA', 'AU', 'SG', 'DE']
const byId = new Map()
let dropped = 0
for (const role of batches.flat().filter(Boolean)) {
  if (!role || !role.id || !role.name || !role.salary) { dropped++; continue }
  const id = String(role.id).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  if (!id || byId.has(id)) { dropped++; continue }
  if (!CC.every(c => Number.isFinite(Number(role.salary[c])) && Number(role.salary[c]) > 0)) { dropped++; continue }
  const skills = (role.skills || []).filter(s => s && s.name && ['A', 'I', 'B'].includes(s.importance))
  if (skills.length < 4) { dropped++; continue }
  const ladder = (role.ladder || []).filter(l => l && l.title && Number.isFinite(Number(l.mult)))
  byId.set(id, {
    id, name: role.name, family: ['eng', 'data', 'infra', 'prod'].includes(role.family) ? role.family : 'eng',
    blurb: role.blurb || '', skills, ladder: ladder.length ? ladder : [{ title: role.name, mult: 1.0 }],
    demand: clampi(role.demand, 20, 99, 60), demandDelta: clampi(role.demandDelta, 0, 60, 10),
    interest: clampi(role.interest, 20, 99, 55), growth: Math.max(0, Math.min(1, Number(role.growth) || 0.3)),
    salary: Object.fromEntries(CC.map(c => [c, clampi(role.salary[c], 1, 99999999, 0)])),
  })
}
const roles = [...byId.values()]
log(`generated ${roles.length} distinct roles (${dropped} dropped as dup/invalid) across ${SLICES.length} slices`)

// ---- skill durability/trend vocab for the union of all skills ----
phase('Generate skill vocab')
const skillNames = [...new Set(roles.flatMap(r => r.skills.map(s => s.name)))].sort()
const SK_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    skills: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          name: { type: 'string' },
          durability: { type: 'integer', description: 'half-life relevance 0-100 (higher=more durable)' },
          trend: { type: 'string', enum: ['rising', 'stable', 'fading'] },
        },
        required: ['name', 'durability', 'trend'],
      },
    },
  },
  required: ['skills'],
}
const chunk = (arr, n) => { const o = []; for (let i = 0; i < arr.length; i += n) o.push(arr.slice(i, i + n)); return o }
const vocabBatches = await parallel(chunk(skillNames, 55).map((names, i) => () =>
  agent(
    `For each tech SKILL below, give a durability score 0-100 (how long the skill stays relevant / its half-life — SQL/System Design are very durable ~90, a hot-but-young framework ~55, a fading tool ~40) and a trend (rising | stable | fading), from your knowledge of the tech market.\n\nSKILLS:\n${names.join(', ')}\n\nReturn ONLY the JSON with every skill.`,
    { label: `skillvocab:${i}`, phase: 'Generate skill vocab', schema: SK_SCHEMA }
  ).then(r => (r && r.skills) ? r.skills : [])
))
const skillVocab = {}
for (const s of vocabBatches.flat().filter(Boolean)) {
  if (s && s.name) skillVocab[s.name] = [clampi(s.durability, 0, 100, 60), ['rising', 'stable', 'fading'].includes(s.trend) ? s.trend : 'stable']
}
log(`skill vocab: ${Object.keys(skillVocab).length} skills scored`)

return { roles, skillVocab, stats: { roles: roles.length, dropped, skills: Object.keys(skillVocab).length } }
