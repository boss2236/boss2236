#!/usr/bin/env python3
"""
Self-hosted profile card generator.

Fetches live data from the GitHub GraphQL API and renders every stat card as
an SVG committed straight into this repo. Nothing on the page depends on a
third-party rendering service.

That is the whole point. The popular public instances are hobby-tier Vercel
deployments that run out of quota: at the time of writing
github-readme-stats returns 503, and profile-trophy, contributor-stats and
nirzak-streak-stats all return 402 Payment Required. Cards served from
raw.githubusercontent.com cannot rate-limit, cannot 402, and cannot vanish.

Stdlib only - no pip install step in CI.

Env:
    GH_TOKEN   required. The workflow's automatic GITHUB_TOKEN is enough for
               public data. Supply a personal access token instead to include
               private repositories in the counts.
    GH_LOGIN   target username (default: boss2236)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

API = "https://api.github.com/graphql"
LOGIN = os.environ.get("GH_LOGIN", "boss2236")
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# ---------------------------------------------------------------- palette

THEMES = {
    "dark": {
        "bg": "#0A101F", "panel": "#111a2e", "border": "#1e2b45",
        "text": "#e6edf7", "dim": "#8899b3",
        "accent": "#00C58E", "accent2": "#0083B0",
        "empty": "#1b2537",
    },
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fc", "border": "#d8e0ee",
        "text": "#12203a", "dim": "#5b6b86",
        "accent": "#00966F", "accent2": "#00668C",
        "empty": "#e7edf6",
    },
}

HEAT = {
    "dark":  ["#1b2537", "#0e4f42", "#12806a", "#00b083", "#3ff0bd"],
    "light": ["#e7edf6", "#c3ecdf", "#7fd9bd", "#33b894", "#00966F"],
}

# ---------------------------------------------------------------- fetch

QUERY = """
query($login: String!) {
  user(login: $login) {
    name login avatarUrl createdAt
    followers { totalCount }
    following { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      nodes {
        name isPrivate stargazerCount forkCount pushedAt
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch(token: str) -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": QUERY, "variables": {"login": LOGIN}}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{LOGIN}-profile-builder",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if "errors" in payload:
        raise SystemExit(f"GraphQL error: {payload['errors']}")
    return payload["data"]["user"]


# ---------------------------------------------------------------- derive

def calendar_days(user: dict) -> list[tuple[date, int]]:
    days = []
    for wk in user["contributionsCollection"]["contributionCalendar"]["weeks"]:
        for d in wk["contributionDays"]:
            days.append((date.fromisoformat(d["date"]), d["contributionCount"]))
    return sorted(days)


def streaks(days: list[tuple[date, int]]) -> tuple[int, int]:
    """(current, longest). Today counts as neutral if still empty - you have
    not broken a streak at 09:00, you just have not committed yet."""
    longest = run = 0
    for _, c in days:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)

    today = datetime.now(timezone.utc).date()
    current = 0
    for d, c in reversed(days):
        if d > today:
            continue
        if c > 0:
            current += 1
        elif d == today:
            continue          # grace day
        else:
            break
    return current, longest


def languages(user: dict, top: int = 6) -> list[tuple[str, str, float]]:
    totals: dict[str, list] = {}
    for repo in user["repositories"]["nodes"]:
        for e in repo["languages"]["edges"]:
            name = e["node"]["name"]
            slot = totals.setdefault(name, [e["node"]["color"] or "#888", 0])
            slot[1] += e["size"]
    grand = sum(v[1] for v in totals.values()) or 1
    ranked = sorted(totals.items(), key=lambda kv: -kv[1][1])[:top]
    return [(n, v[0], v[1] / grand * 100.0) for n, v in ranked]


def summarize(user: dict) -> dict:
    repos = user["repositories"]["nodes"]
    cc = user["contributionsCollection"]
    days = calendar_days(user)
    cur, longest = streaks(days)
    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - created).days
    return {
        "login": user["login"],
        "name": (user["name"] or user["login"]).strip(),
        "public_repos": sum(1 for r in repos if not r["isPrivate"]),
        "private_repos": sum(1 for r in repos if r["isPrivate"]),
        "total_repos": len(repos),
        "stars": sum(r["stargazerCount"] for r in repos),
        "forks": sum(r["forkCount"] for r in repos),
        "followers": user["followers"]["totalCount"],
        "commits": cc["totalCommitContributions"],
        "prs": cc["totalPullRequestContributions"],
        "issues": cc["totalIssueContributions"],
        "restricted": cc["restrictedContributionsCount"],
        "contributions": cc["contributionCalendar"]["totalContributions"],
        "streak": cur,
        "longest": longest,
        "age_days": age_days,
        "age_months": round(age_days / 30.44),
        "languages": languages(user),
        "days": days,
        "generated": datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
    }


# ---------------------------------------------------------------- render

def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def shell(w: int, h: int, t: dict, title: str, body: str, extra_css: str = "") -> str:
    """Common card chrome: rounded panel, accent rule, title, animated body."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"
     viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">
  <style>
    .bg   {{ fill:{t['panel']}; stroke:{t['border']}; stroke-width:1; }}
    .ttl  {{ fill:{t['text']}; font:600 14px 'Segoe UI',Ubuntu,Helvetica,sans-serif; }}
    .lbl  {{ fill:{t['dim']};  font:400 11px 'Segoe UI',Ubuntu,Helvetica,sans-serif; }}
    .val  {{ fill:{t['text']}; font:700 22px 'Segoe UI',Ubuntu,Helvetica,sans-serif; }}
    .acc  {{ fill:{t['accent']}; }}
    /* fill-mode is 'backwards', never 'forwards' with a 0-opacity base rule.
       An SVG embedded as an image may never get to run its animation -
       reduced motion, a still renderer, a screenshot pipeline - and it must
       still show the numbers. Base state is therefore fully visible; the
       animation only borrows the hidden state during its delay.
       Note: no angle brackets in this comment. Style content is parsed as
       XML markup, so a stray tag name here kills the whole document. */
    .rise {{ animation:rise .6s ease-out backwards; }}
    @keyframes rise {{ from {{ opacity:0; transform:translateY(6px); }}
                        to {{ opacity:1; transform:translateY(0); }} }}
    @media (prefers-reduced-motion: reduce) {{
      .rise {{ animation:none; }}
    }}
    {extra_css}
  </style>
  <rect class="bg" x=".5" y=".5" width="{w-1}" height="{h-1}" rx="10"/>
  <rect x="0" y="0" width="4" height="{h}" rx="2" fill="{t['accent']}"/>
  <text class="ttl" x="20" y="30">{esc(title)}</text>
{body}
</svg>
"""


def card_stats(s: dict, theme: str) -> str:
    t = THEMES[theme]
    cells = [
        ("Repositories", s["total_repos"]),
        ("Commits", s["commits"]),
        ("Contributions", s["contributions"]),
        ("Pull requests", s["prs"]),
        ("Issues", s["issues"]),
        ("Followers", s["followers"]),
    ]
    w, h = 460, 178
    out = []
    for i, (label, value) in enumerate(cells):
        cx = 24 + (i % 3) * 148
        cy = 78 + (i // 3) * 62
        delay = 0.06 * i
        out.append(
            f'  <g class="rise" style="animation-delay:{delay:.2f}s">\n'
            f'    <text class="val" x="{cx}" y="{cy}">{value}</text>\n'
            f'    <text class="lbl" x="{cx}" y="{cy+17}">{esc(label)}</text>\n'
            f'  </g>'
        )
    sub = (f'  <text class="lbl" x="20" y="48">building for {s["age_months"]} months '
           f'&#183; {s["streak"]} day streak &#183; best {s["longest"]}</text>')
    return shell(w, h, t, "Activity", sub + "\n" + "\n".join(out))


def card_langs(s: dict, theme: str) -> str:
    t = THEMES[theme]
    w, h = 460, 178
    bar_x, bar_y, bar_w = 20, 62, w - 40
    parts, legend, css = [], [], []
    x = bar_x
    for i, (name, color, pct) in enumerate(s["languages"]):
        seg = bar_w * pct / 100.0
        css.append(f".s{i}{{animation:grow{i} .9s ease-out backwards;transform-origin:left;}}"
                   f"@keyframes grow{i}{{from{{transform:scaleX(0)}}to{{transform:scaleX(1)}}}}")
        parts.append(f'  <rect class="s{i}" x="{x:.1f}" y="{bar_y}" width="{seg:.1f}" '
                     f'height="14" fill="{color}" style="animation-delay:{i*0.08:.2f}s"/>')
        col, row = i % 2, i // 2
        lx, ly = bar_x + col * 220, 108 + row * 26
        legend.append(
            f'  <g class="rise" style="animation-delay:{0.3+i*0.06:.2f}s">\n'
            f'    <circle cx="{lx+5}" cy="{ly-4}" r="5" fill="{color}"/>\n'
            f'    <text class="lbl" x="{lx+18}" y="{ly}">{esc(name)} '
            f'<tspan fill="{t["dim"]}">{pct:.1f}%</tspan></text>\n  </g>')
        x += seg
    body = (f'  <text class="lbl" x="20" y="48">by bytes across '
            f'{s["total_repos"]} repositories</text>\n'
            f'  <clipPath id="r"><rect x="{bar_x}" y="{bar_y}" width="{bar_w}" '
            f'height="14" rx="7"/></clipPath>\n  <g clip-path="url(#r)">\n'
            + "\n".join(parts) + "\n  </g>\n" + "\n".join(legend))
    return shell(w, h, t, "Languages", body, "\n    ".join(css))


def card_streak(s: dict, theme: str) -> str:
    t = THEMES[theme]
    w, h = 460, 178
    css = (
        ".flame{animation:flicker 1.5s ease-in-out infinite;"
        "transform-box:fill-box;transform-origin:center}"
        "@keyframes flicker{0%,100%{transform:scale(1) rotate(-4deg)}"
        "30%{transform:scale(1.1) rotate(3deg)}60%{transform:scale(.95) rotate(-3deg)}}"
        f".big{{fill:{t['text']};font:700 40px 'Segoe UI',Ubuntu,Helvetica,sans-serif;}}"
    )
    body = (
        '  <text class="flame" x="64" y="110" font-size="44" text-anchor="middle">&#128293;</text>\n'
        f'  <text class="big" x="150" y="114">{s["streak"]}</text>\n'
        f'  <text class="lbl" x="152" y="130">day streak</text>\n'
        f'  <rect x="120" y="140" width="320" height="1" fill="{t["border"]}"/>\n'
        f'  <text class="lbl" x="120" y="158">&#128293; best {s["longest"]} days</text>\n'
        f'  <text class="lbl" x="300" y="158">&#128197; {s["contributions"]} contributions this year</text>'
    )
    return shell(w, h, t, "Streak", body, css)


def card_trophies(s: dict, theme: str) -> str:
    t = THEMES[theme]
    w, h = 460, 178
    cells = [
        ("&#11088;", s["stars"], "Stars"),
        ("&#127843;", s["forks"], "Forks"),
        ("&#128101;", s["followers"], "Followers"),
        ("&#127919;", s["prs"], "PRs"),
        ("&#128027;", s["issues"], "Issues"),
        ("&#128197;", s["age_months"], "Months"),
    ]
    css = (
        f".chip{{fill:{t['panel']};stroke:{t['border']};stroke-width:1;}}"
        f".chipval{{fill:{t['text']};font:700 16px 'Segoe UI',Ubuntu,Helvetica,sans-serif;}}"
        f".chiplbl{{fill:{t['dim']};font:400 10px 'Segoe UI',Ubuntu,Helvetica,sans-serif;}}"
    )
    out = []
    for i, (emoji, value, label) in enumerate(cells):
        cx = 24 + (i % 3) * 148
        cy = 66 + (i // 3) * 64
        out.append(
            f'  <g class="rise" style="animation-delay:{0.1 + i * 0.08:.2f}s">\n'
            f'    <rect x="{cx}" y="{cy - 48}" width="136" height="52" rx="10" class="chip"/>\n'
            f'    <text x="{cx + 14}" y="{cy - 31}" font-size="15">{emoji}</text>\n'
            f'    <text class="chipval" x="{cx + 36}" y="{cy - 18}">{value}</text>\n'
            f'    <text class="chiplbl" x="{cx + 36}" y="{cy - 6}">{label}</text>\n'
            f'  </g>'
        )
    return shell(w, h, t, "Milestones", "\n".join(out), css)


def card_heatmap(s: dict, theme: str, weeks: int = 26) -> str:
    t, heat = THEMES[theme], HEAT[theme]
    days = s["days"][-weeks * 7:]
    cell, gap = 13, 3
    top = 58                      # first grid row
    w = 40 + weeks * (cell + gap)
    h = top + 7 * (cell + gap) + 36   # 7 rows + room for the legend strip
    peak = max((c for _, c in days), default=0) or 1

    squares = []
    for idx, (d, c) in enumerate(days):
        col, row = idx // 7, idx % 7
        lvl = 0 if c == 0 else min(4, 1 + int(c / peak * 3.999))
        x = 20 + col * (cell + gap)
        y = top + row * (cell + gap)
        squares.append(
            f'  <rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" '
            f'fill="{heat[lvl]}" class="cellf" style="animation-delay:{col*0.012:.3f}s">'
            f'<title>{d.isoformat()}: {c}</title></rect>')

    key = []
    kx = w - 130
    for i, c in enumerate(heat):
        key.append(f'  <rect x="{kx+i*16}" y="{h-26}" width="11" height="11" rx="2" fill="{c}"/>')
    body = (f'  <text class="lbl" x="20" y="48">{s["contributions"]} contributions '
            f'in the last year</text>\n' + "\n".join(squares) + "\n"
            f'  <text class="lbl" x="{kx-34}" y="{h-17}">less</text>\n'
            + "\n".join(key) +
            f'\n  <text class="lbl" x="{kx+84}" y="{h-17}">more</text>')
    css = (".cellf{animation:pop .5s ease-out backwards}"
           "@keyframes pop{from{opacity:0}to{opacity:1}}")
    return shell(w, h, t, "Contribution graph", body, css)


# ---------------------------------------------------------------- readme

def inject(readme: str, key: str, value: str) -> str:
    a, b = f"<!-- {key}:START -->", f"<!-- {key}:END -->"
    pat = re.compile(re.escape(a) + r".*?" + re.escape(b), re.S)
    if not pat.search(readme):
        print(f"  ! marker {key} not found, skipped")
        return readme
    return pat.sub(f"{a}\n{value}\n{b}", readme)


def picture(stem: str, alt: str, svgs: dict[str, str], width: str = "100%") -> str:
    """Theme-aware <picture> with a content hash on each URL.

    The hash is the cache-buster. GitHub proxies README images through camo,
    which keys on URL - regenerate a card in place and the old bytes can keep
    being served for hours. Changing the query string whenever the content
    changes sidesteps that without touching the cache headers.
    """
    def h(theme: str) -> str:
        body = svgs[f"{stem}-{theme}"].encode("utf-8")
        return (f"assets/{stem}-{theme}.svg"
                f"?v={hashlib.sha256(body).hexdigest()[:8]}")

    return (f'<picture>\n'
            f'  <source media="(prefers-color-scheme: dark)" srcset="{h("dark")}" />\n'
            f'  <source media="(prefers-color-scheme: light)" srcset="{h("light")}" />\n'
            f'  <img src="{h("dark")}" alt="{esc(alt)}" width="{width}" />\n'
            f'</picture>')


def readme_blocks(s: dict, svgs: dict[str, str]) -> dict[str, str]:
    langs = " &#183; ".join(f"**{n}** {p:.0f}%" for n, _, p in s["languages"][:4])
    cards = (
        "<div align=\"center\">\n\n"
        + picture("stats", "Activity summary", svgs, "49%") + "\n"
        + picture("langs", "Language breakdown", svgs, "49%") + "\n\n"
        + picture("streak", "Contribution streak", svgs, "49%") + "\n"
        + picture("trophies", "Milestones", svgs, "49%") + "\n\n"
        + picture("heatmap", "Contribution graph", svgs, "96%") + "\n\n"
        + "</div>"
    )
    return {
        "CARDS": cards,
        "SNAPSHOT": (
            f"| | | |\n|:--|:--|:--|\n"
            f"| **{s['total_repos']}** repositories "
            f"| **{s['commits']}** commits this year "
            f"| **{s['contributions']}** contributions |\n"
            f"| **{s['streak']}** day streak "
            f"| **{s['longest']}** day best "
            f"| **{s['age_months']}** months building |\n"
            f"| **{s['stars']}** stars "
            f"| **{s['followers']}** followers "
            f"| **{s['prs']}** PRs |"
        ),
        "LANGS": langs,
        "UPDATED": (
            f"<sub>Cards regenerated automatically &#183; last run "
            f"**{s['generated']}**</sub>"
        ),
    }


# ---------------------------------------------------------------- main

def main() -> int:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN not set", file=sys.stderr)
        return 1

    try:
        user = fetch(token)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read()[:300]!r}", file=sys.stderr)
        return 1

    s = summarize(user)
    ASSETS.mkdir(exist_ok=True)

    # Render everything in memory first, so we can decide whether anything
    # actually moved before touching the working tree. The "last run" stamp
    # changes on every single run by definition; if that were allowed to
    # count as a change, the workflow would commit four times a day forever
    # and bury the real history in noise.
    svgs = {f"{stem}-{theme}": fn(s, theme)
            for theme in ("dark", "light")
            for stem, fn in (("stats", card_stats), ("langs", card_langs),
                             ("heatmap", card_heatmap),
                             ("streak", card_streak),
                             ("trophies", card_trophies))}

    rp = ROOT / "README.md"
    md = rp.read_text(encoding="utf-8")
    blocks = readme_blocks(s, svgs)
    new_md = md
    for k, v in blocks.items():
        new_md = inject(new_md, k, v)

    def without_stamp(text: str) -> str:
        return re.sub(r"<!-- UPDATED:START -->.*?<!-- UPDATED:END -->", "",
                      text, flags=re.S)

    svg_changed = [k for k, body in svgs.items()
                   if not (ASSETS / f"{k}.svg").exists()
                   or (ASSETS / f"{k}.svg").read_text(encoding="utf-8") != body]
    md_changed = without_stamp(new_md) != without_stamp(md)

    if not svg_changed and not md_changed:
        print("no change since last run - nothing written")
        return 0

    for k, body in svgs.items():
        p = ASSETS / f"{k}.svg"
        p.write_text(body, encoding="utf-8")
    print(f"  wrote {len(svgs)} cards ({len(svg_changed)} changed)")
    rp.write_text(new_md, encoding="utf-8")

    if s["private_repos"] == 0 and s["restricted"] == 0:
        print("  note: token sees public data only - private repos excluded")
    print(f"\n{s['name']} (@{s['login']}): {s['total_repos']} repos "
          f"({s['private_repos']} private), {s['contributions']} contributions, "
          f"streak {s['streak']}/{s['longest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
