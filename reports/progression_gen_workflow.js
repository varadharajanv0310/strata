export const meta = {
  name: 'strata-generate-progression',
  description: 'Generate early-career headline + full salary progression + trajectory per role x 7 countries (the catalog IS the source)',
  phases: [{ title: 'Generate progression' }],
}

// The catalog is generated from model knowledge. For each EXISTING role, generate the full
// early->senior salary PROGRESSION per country. The FIRST stage is the early-career (entry)
// figure = the new headline. No dataset; the model is the source.

const roles = typeof args === 'string' ? JSON.parse(args) : args

const ANCHOR = [
  'Salary CALIBRATION - full-time annual MEDIAN total comp per stage, LOCAL currency',
  '(India in absolute INR e.g. 900000 = 9 LPA; US USD; UK GBP; Canada CAD; Australia AUD;',
  'Singapore SGD; Germany EUR). The EARLY stage is what a NEW entrant (0-2 yrs) actually',
  'starts on - NOT a blended average. Reference (Software Engineer):',
  '  early(0-2y): IN 700000  US 115000 GB 48000  CA 85000  AU 85000  SG 65000  DE 55000',
  '  mid(2-5y):   IN 1600000 US 150000 GB 68000  CA 120000 AU 130000 SG 105000 DE 75000',
  '  senior(5-9): IN 3200000 US 200000 GB 95000  CA 155000 AU 165000 SG 150000 DE 95000',
  '  staff(9y+):  IN 5500000 US 270000 GB 130000 CA 210000 AU 220000 SG 210000 DE 120000',
  'Scale each role realistically: ML/AI/security/quant specialists carry a premium;',
  'support/QA/junior-heavy roles sit lower; India keeps a larger discount for support/ops',
  'and a smaller one for elite AI/quant. Each stage median MUST increase with experience.',
].join('\n')

const SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    roles: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          id: { type: 'string' },
          stages: {
            type: 'array', minItems: 3, maxItems: 6,
            items: {
              type: 'object', additionalProperties: false,
              properties: {
                title: { type: 'string' },
                yoe_min: { type: 'integer' },
                yoe_max: { type: 'integer' },
                salary: {
                  type: 'object', additionalProperties: false,
                  properties: {
                    IN: { type: 'integer' }, US: { type: 'integer' }, GB: { type: 'integer' },
                    CA: { type: 'integer' }, AU: { type: 'integer' }, SG: { type: 'integer' }, DE: { type: 'integer' },
                  },
                  required: ['IN', 'US', 'GB', 'CA', 'AU', 'SG', 'DE'],
                },
              },
              required: ['title', 'yoe_min', 'yoe_max', 'salary'],
            },
          },
          leads_to: { type: 'array', minItems: 1, maxItems: 5, items: { type: 'string' } },
        },
        required: ['id', 'stages', 'leads_to'],
      },
    },
  },
  required: ['roles'],
}

const BATCH = 20
const batches = []
for (let i = 0; i < roles.length; i += BATCH) batches.push(roles.slice(i, i + BATCH))

phase('Generate progression')
const results = await parallel(batches.map((batch, bi) => () => {
  const roleLines = batch.map(r => '- ' + r.id + ': ' + r.name + ' (' + r.family + ')').join('\n')
  const prompt = 'Generate the salary PROGRESSION + career trajectory for each of these tech roles, '
    + 'from your knowledge of the global tech job market. This is the product - accurate figures a '
    + 'person deciding a career path relies on.\n\nROLES (use the EXACT id given):\n' + roleLines
    + '\n\n' + ANCHOR
    + '\n\nFor each role produce 4-6 experience STAGES from early-career to the top (entry -> mid -> '
    + 'senior -> staff/lead/principal, fitting the role), each with a rung title, a year-of-experience '
    + 'band (yoe_min/yoe_max), and the 7 per-country MEDIAN salaries in local currency. The FIRST stage '
    + 'is the realistic ENTRY salary a new person actually starts on. Also give leads_to: 1-5 roles/'
    + 'titles this role progresses or branches into. Return ONLY the JSON with every role id.'
  return agent(prompt, { label: 'prog:' + bi, phase: 'Generate progression', schema: SCHEMA })
    .then(r => (r && r.roles) ? r.roles : [])
}))

const CC = ['IN', 'US', 'GB', 'CA', 'AU', 'SG', 'DE']
const clampi = (v, lo, hi, d) => { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : d }
const byId = {}
let dropped = 0
for (const rec of results.flat().filter(Boolean)) {
  if (!rec || !rec.id || !Array.isArray(rec.stages)) { dropped++; continue }
  const stages = rec.stages
    .filter(s => s && s.salary && CC.every(c => Number(s.salary[c]) > 0))
    .map(s => ({
      title: String(s.title || '').slice(0, 80),
      yoe_min: clampi(s.yoe_min, 0, 40, 0),
      yoe_max: clampi(s.yoe_max, 0, 60, 40),
      salary: Object.fromEntries(CC.map(c => [c, clampi(s.salary[c], 1, 999999999, 0)])),
    }))
  if (stages.length < 3) { dropped++; continue }
  for (const c of CC) for (let i = 1; i < stages.length; i++)
    if (stages[i].salary[c] < stages[i - 1].salary[c]) stages[i].salary[c] = stages[i - 1].salary[c]
  const leads_to = (rec.leads_to || []).map(x => String(x).slice(0, 80)).filter(Boolean).slice(0, 5)
  byId[rec.id] = { id: rec.id, stages, leads_to }
}
log('progression generated for ' + Object.keys(byId).length + '/' + roles.length + ' roles (' + dropped + ' dropped)')
return { progression: byId, stats: { generated: Object.keys(byId).length, expected: roles.length, dropped } }
