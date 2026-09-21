# future-os-blog.github.io

Pages site for the FutureOS engineering blog, served at **https://future-os-blog.github.io**.

This repository is a *publisher*, not the source. The posts live in
[`futuregene/future-os`](https://github.com/futuregene/future-os) under
[`docs/blog/`](https://github.com/futuregene/future-os/tree/main/docs/blog), together
with the generator (`scripts/blog/build.py`) and its tests. Nothing here is
hand-edited except [`CNAME`](#custom-domain) — the build clones the sources and
renders them:

```
git clone --depth 1 https://github.com/futuregene/future-os  →  python3 scripts/blog/build.py
```

A Pages artifact can only be deployed by the repository that owns it, which is why
the build runs here instead of in `futuregene/future-os`. The sources are public, so
the clone is anonymous: **no token, no secret**. The trade-off is that publishing is
pull-based — it happens on the schedule in
[`.github/workflows/publish.yml`](.github/workflows/publish.yml) (hourly, `:17`).

## Publishing immediately

```bash
gh workflow run publish.yml                       # build future-os@main
gh workflow run publish.yml -f ref=<branch>       # preview a branch before merge
gh run watch                                      # follow the deploy
```

`-f ref=<branch>` is the way to review a blog change from a pull request in
`futuregene/future-os` before it lands: the same generator and the same tests run,
against an unreleased branch.

## Custom domain

Drop the hostname (no scheme, no path) in a `CNAME` file at the root of this
repository — the workflow copies it into the published output:

```
echo "blog.example.com" > CNAME
```

Then add the DNS record at the registrar: `CNAME  blog.example.com → future-os-blog.github.io`
(GitHub also accepts four `A` records for the apex: `185.199.108.153`,
`185.199.109.153`, `185.199.110.153`, `185.199.111.153`). Finally set the domain under
Settings → Pages and tick "Enforce HTTPS". Because every link in the build is relative,
moving to a custom domain is a DNS/config change — nothing in the sources has to move.

## Site configuration that is *not* here

- Post front matter, the markdown subset, the draft flag and the local preview
  commands: [`docs/blog/README.md`](https://github.com/futuregene/future-os/blob/main/docs/blog/README.md).
- Title, tagline, feed size: [`docs/blog/blog.json`](https://github.com/futuregene/future-os/blob/main/docs/blog/blog.json).
  This workflow overrides only `base_url` (feed, sitemap, canonical tags).
