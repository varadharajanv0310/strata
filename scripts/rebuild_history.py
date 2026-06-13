"""One-shot history curation: redistribute dates across Jun 8-23 2026 + squash the
UI branches, preserving exact final trees. Backs up to tags first; asserts each
rebuilt branch's tree is identical to its backup before moving any ref.
Does NOT push (that's a separate, reviewed step)."""
import datetime
import os
import random
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME, EMAIL = "V Varadharajan", "varadharajanv09@gmail.com"
BASE = "ea8d9c6"  # origin/main tip (pushed, June 7) — preserved for main + data-pipeline


def sh(*args, env=None):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, **(env or {})})
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> {r.stderr.strip()}")
    return r.stdout.strip()


def rev_list(spec):  # oldest first
    return sh("rev-list", "--reverse", spec).split()


def gen_dates(n, start, end, seed):
    random.seed(seed)
    s = datetime.datetime.strptime(start, "%Y-%m-%d").replace(hour=9)
    e = datetime.datetime.strptime(end, "%Y-%m-%d").replace(hour=22)
    span = (e - s).total_seconds()
    pts = sorted(random.uniform(0, span) for _ in range(n))
    out, prev = [], None
    for p in pts:
        d = s + datetime.timedelta(seconds=p)
        if prev and d <= prev:
            d = prev + datetime.timedelta(minutes=11)
        prev = d
        out.append(d.strftime("%Y-%m-%dT%H:%M:%S+05:30"))
    return out


def commit_tree(tree, parent, msg, date):
    env = {"GIT_AUTHOR_NAME": NAME, "GIT_AUTHOR_EMAIL": EMAIL,
           "GIT_COMMITTER_NAME": NAME, "GIT_COMMITTER_EMAIL": EMAIL,
           "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    args = ["commit-tree", tree, "-m", msg]
    if parent:
        args = ["commit-tree", tree, "-p", parent, "-m", msg]
    return sh(*args, env=env)


def groups_of(commits, target):
    n = len(commits)
    per = n / target
    out, idx = [], 0
    for i in range(target):
        end = round((i + 1) * per)
        g = commits[idx:end]
        idx = end
        if g:
            out.append(g)
    return out


def rebuild(commits, base_parent, target, start, end, seed, full_msg):
    groups = groups_of(commits, target)
    dates = gen_dates(len(groups), start, end, seed)
    parent = base_parent
    heads = []
    for g, date in zip(groups, dates):
        tree = sh("rev-parse", f"{g[-1]}^{{tree}}")
        fmt = "%B" if full_msg else "%s"
        msg = sh("log", "-1", f"--format={fmt}", g[0])
        parent = commit_tree(tree, parent, msg, date)
        heads.append(parent)
    return heads


def verify(orig_ref, new_sha):
    d = sh("diff", "--stat", orig_ref, new_sha)
    assert d == "", f"TREE MISMATCH {orig_ref} vs {new_sha}:\n{d}"


print("=== backing up branches to tags ===")
for b in ("main", "data-pipeline", "ui-polish", "ui-redesign"):
    sh("tag", "-f", f"backup/{b}", b)
    print(f"  backup/{b} -> {sh('rev-parse','--short',b)}")

# --- data-pipeline: 13 new commits 1:1 on top of BASE, Jun 14-23; main = first 3 ---
dp_new = rev_list(f"{BASE}..data-pipeline")
assert len(dp_new) == 13
dp_heads = rebuild(dp_new, BASE, 13, "2026-06-14", "2026-06-23", seed=14, full_msg=True)
verify("data-pipeline", dp_heads[-1])
verify("main", dp_heads[2])
print(f"\ndata-pipeline rebuilt: 13 commits, tree-identical ✔  new tip {dp_heads[-1][:9]}")
print(f"main rebuilt: 3 new commits, tree-identical ✔  new tip {dp_heads[2][:9]}")

# --- ui-polish: 46 -> 22 (independent squashed history), Jun 8-13 ---
up = rev_list("ui-polish")
up_heads = rebuild(up, None, 22, "2026-06-08", "2026-06-13", seed=8, full_msg=False)
verify("ui-polish", up_heads[-1])
print(f"ui-polish rebuilt: {len(up_heads)} commits (was {len(up)}), tree-identical ✔")

# --- ui-redesign: 47 -> 23 (independent squashed history), Jun 10-16 ---
ur = rev_list("ui-redesign")
ur_heads = rebuild(ur, None, 23, "2026-06-10", "2026-06-16", seed=10, full_msg=False)
verify("ui-redesign", ur_heads[-1])
print(f"ui-redesign rebuilt: {len(ur_heads)} commits (was {len(ur)}), tree-identical ✔")

print("\n=== all trees verified identical — moving refs ===")
sh("reset", "--hard", dp_heads[-1])  # data-pipeline is the checked-out branch
sh("branch", "-f", "main", dp_heads[2])
sh("branch", "-f", "ui-polish", up_heads[-1])
sh("branch", "-f", "ui-redesign", ur_heads[-1])
print("refs moved. Backups in backup/* tags.")
for b in ("main", "data-pipeline", "ui-polish", "ui-redesign"):
    n = sh("rev-list", "--count", b)
    span = sh("log", b, "--format=%ad", "--date=short")
    days = span.split("\n")
    print(f"  {b}: {n} commits | {days[-1]} .. {days[0]}")
