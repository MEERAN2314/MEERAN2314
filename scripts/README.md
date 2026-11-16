# How the profile cards are built

Every card in `cards/` is an animated SVG rendered by one script, `render_cards.py`, and shown on the profile
through a plain `<img>` tag. The SVGs use only an inline CSS `<style>` block, SMIL `<animate>` elements and Fira Code
subset with `pyftsubset` and embedded as base64 WOFF, so nothing is fetched from outside GitHub. Every animation is
switched off under `prefers-reduced-motion`.

| Piece | What it does |
| --- | --- |
| `.github/workflows/cards.yml` | Runs daily: refreshes the data, renders the cards, commits any change |
| `scripts/fetch_contributions.py` | Writes `data/contributions.json`: GitHub's calendar from the GraphQL API, plus your commits on every branch from the REST API |
| `scripts/render_cards.py` | Renders `cards/*.svg`. `STATIC=1` renders the final frame only, for screenshots |
| `scripts/vectorize_portrait.py` | Optional: traces a photo into `assets/portrait.svg` (needs `opencv-python-headless`) |
| `cards.config.json` | Name, tagline, colours, links, tech stack and settings for the all-branch graph |

## Preview locally

```bash
pip install fonttools
python scripts/fetch_contributions.py --sample   # fake data, no token needed
STATIC=1 python scripts/render_cards.py          # final frame
open cards/_preview.html                         # same layout as the profile
```

## All-branch graph

The second graph counts every commit you authored on any branch of your repos, once per commit. GitHub's own graph
ignores commits that are not on a default branch. The default workflow token sees public repos only; add a
`PROFILE_TOKEN` secret (a personal access token with `repo` and `read:user`) to include private and organisation repos.
Settings live under `all_branch` in `cards.config.json`.

The third graph, "Combined activity", is GitHub's count for each day plus the commits that GitHub ignores because they are not on a default branch. Commits that are on both a feature branch and the default branch are only counted once. One caveat: if you merge pull requests with squash or rebase, the original branch commits are still on the branch and are counted as extra on top of the squashed commit, so busy days can read slightly high.

If something goes wrong the refresh does not fail: a repo or branch that cannot be read is skipped with a warning, a
failed scan keeps the previous result, and a graph with no data yet shows an empty grid.

Fira Code is licensed under the SIL Open Font License, see `assets/fonts/OFL-LICENSE.txt`.
