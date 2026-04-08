# Polyglot AI wiki pages

This folder contains the Markdown source for the GitHub wiki of the
Polyglot AI project. The pages follow the GitHub wiki page-name
convention (one `.md` per page, wiki-style cross-links without the `.md`
extension).

## How to publish these to the GitHub wiki

GitHub wikis are a separate git repository attached to the main repo:

```
https://github.com/<owner>/polyglot-ai.wiki.git
```

To push these pages:

```bash
# From outside this repo
git clone https://github.com/<owner>/polyglot-ai.wiki.git
cd polyglot-ai.wiki

# Copy the pages in
cp -f /path/to/polyglot-ai/wiki/*.md .

# Commit and push
git add .
git commit -m "Add full user guide"
git push
```

The first time you push, the wiki must exist on GitHub — go to the
repo's **Wiki** tab and click **Create the first page** (any content),
then clone and replace.

## Pages

- `Home.md` — landing page and table of contents
- `Getting-Started.md`
- `Chat.md`
- `Editor-Terminal-Files.md`
- `Git-and-PR.md`
- `Tests-and-Review.md`
- `CI-Docker-Kubernetes.md`
- `Database.md`
- `MCP-Servers.md`
- `Tasks-and-Today.md`
- `Settings-and-Shortcuts.md`
- `Troubleshooting-FAQ.md`

Cross-links use the wiki format `[Label](Page-Name)` (no `.md`).
