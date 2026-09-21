# future-os-blog.github.io

Source **and** the published site of the FutureOS engineering blog, served at
**https://future-os-blog.github.io**.

Markdown in, static site out: [`scripts/build.py`](scripts/build.py) renders
[`blog/`](blog/) into plain HTML/CSS and
[`.github/workflows/publish.yml`](.github/workflows/publish.yml) deploys it to
GitHub Pages. The generator is stdlib-only Python — no npm, no pip, no build
dependencies to update.

## Layout

| Path | What it is |
|---|---|
| `blog/blog.json` | Site config: title, tagline, `base_url`, repo link, feed size |
| `blog/posts/YYYY-MM-DD-slug.md` | One file per post — the date and URL slug come from the filename |
| `blog/assets/` | `blog.css`, `blog.js` and any images, copied verbatim into the build |
| `scripts/build.py` | The generator (index, post pages, tag pages, RSS, sitemap) |
| `scripts/test_build.py` | Its regression tests |

Generated output goes to `build/` and is never committed.

## Writing a post

1. Create `blog/posts/YYYY-MM-DD-slug.md` — the date and the slug come from the
   filename (a file without the date prefix must set `date:` in its front matter).
2. Write front matter, then the body:

   ```markdown
   ---
   title: Why the agent keeps a per-user lock
   date: 2026-09-21
   tags: [agent, concurrency]
   summary: One process per user, and why the lock is not negotiable.
   author: FutureOS        # optional, defaults to blog.json `author`
   draft: true             # optional — excluded from the published site
   ---

   Body markdown follows.
   ```

   `title` and a date are required; `tags`, `summary` and `author` are optional
   (`summary` falls back to the first 220 characters of the body). Unknown keys
   are rejected rather than ignored, so a typo cannot silently drop a tag.

3. Preview and publish:

   ```bash
   python3 scripts/build.py --include-drafts        # → build/, drafts included
   python3 -m http.server 4321 --directory build   # http://127.0.0.1:4321
   python3 scripts/test_build.py                    # generator regression tests
   ```

   Commit and push to `main` and the site redeploys (or run the workflow by hand
   from the Actions tab).

Markdown is a deliberate subset — headings, fenced code, blockquotes, nested
lists, GFM pipe tables, rules, and inline code/emphasis/links/images. Raw HTML is
escaped rather than executed. Images live in `blog/assets/` and are referenced as
`../assets/screenshot.png` (which resolves identically from the source file and
from the rendered page).

**Links must survive publication.** Only `build/` is served, so a relative link
to anything outside the blog would work in the source tree and 404 on the site.
The build rejects one, and only one, relative form — `../assets/…`; link
repository material by absolute URL and publish any image you need under
`blog/assets/`.

## Custom domain

Drop the hostname (no scheme, no path) in a `CNAME` file at the root of this
repository — the workflow copies it into the published output:

```bash
echo "blog.example.com" > CNAME
```

Then add the DNS record at the registrar:
`CNAME  blog.example.com → future-os-blog.github.io`
(GitHub also accepts four `A` records for an apex domain: `185.199.108.153`,
`185.199.109.153`, `185.199.110.153`, `185.199.111.153`). Finally set the domain
under Settings → Pages and tick "Enforce HTTPS". Every link in the build is
relative, so switching address is a DNS/config change — no source edit, apart
from `base_url` in `blog/blog.json`, which drives the feed, the sitemap and
canonical tags.
