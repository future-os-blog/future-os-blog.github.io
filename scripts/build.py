#!/usr/bin/env python3
"""Build the FutureOS engineering blog into a static site.

Source of truth: ``blog/`` in this repository. Output: a self-contained
directory of HTML/CSS/assets whose internal links are *all relative*, so the
same build serves unchanged from a project-page path, a bare Pages domain or a
custom domain. Only ``base_url`` — used for the feed, the sitemap and
canonical tags — has to match the final address.

    python3 scripts/build.py                       # → build/
    python3 scripts/build.py --out /tmp/site --include-drafts

Stdlib only: the publish workflow runs it on the runner's stock ``python3``,
so no npm/pip install is needed for the Pages job. The pure functions (front
matter, markdown, slugging, summaries) are locked by ``scripts/test_build.py``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLOG_DIR = REPO_ROOT / "blog"
DEFAULT_OUT_DIR = REPO_ROOT / "build"

DEFAULT_CONFIG = {
    "title": "FutureOS Engineering",
    "tagline": "Notes from building one agent everywhere",
    "description": "Engineering notes from the FutureOS team.",
    "base_url": "https://futuregene.github.io/future-os",
    "author": "FutureOS",
    "repo_url": "https://github.com/futuregene/future-os",
    "site_repo": "",
    "repo_branch": "main",
    "feed_size": 20,
    "i18n": {},
}

# ── Markdown ────────────────────────────────────────────────────────────────
# A deliberate subset, not CommonMark: headings, fenced code, blockquotes,
# lists (nested), GFM pipe tables, rules, and the usual inline spans. Anything
# outside it stays literal text, which is the safe failure mode for a blog.

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([\w+#.-]*)[ \t]*$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
RULE_RE = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
LIST_RE = re.compile(r"^( *)([-*+]|\d{1,9}[.)])[ \t]+(?:\[(?P<check>[ xX])\][ \t]+)?(.*)$")
TABLE_SEP_RE = re.compile(r"^ {0,3}\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$")
QUOTE_RE = re.compile(r"^ {0,3}> ?(.*)$")
INLINE_RE = re.compile(
    r"(?P<code>`+)(?P<code_body>.+?)(?P=code)"
    r"|!\[(?P<img_alt>[^\]]*)\]\((?P<img_url>[^\s)]+)(?:\s+[\"'](?P<img_title>[^\"']*)[\"'])?\)"
    r"|\[(?P<link_text>[^\]]*)\]\((?P<link_href>[^\s)]+)(?:\s+[\"'](?P<link_title>[^\"']*)[\"'])?\)"
    r"|\*\*(?P<strong>.+?)\*\*"
    r"|__(?P<strong_>[\s\S]+?)__"
    r"|~~(?P<strike>[^~]+)~~"
    r"|\*(?P<em>[^*\s][^*]*?)\*"
    r"|_(?P<em_>[\s\S]+?)_",
    re.S,
)

# Progressively literal fallbacks for a summary excerpt, applied in order.
SUMMARY_STRIP = (
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"^ {0,3}#{1,6}[ \t]+.*$", re.M), " "),
    (re.compile(r"[*_~]{1,2}"), ""),
)


def slugify(text: str, fallback: str = "section") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or fallback


def excerpt(body: str, limit: int = 220) -> str:
    text = body
    for pattern, replacement in SUMMARY_STRIP:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


class MarkdownRenderer:
    """Block+inline renderer for the blog's markdown subset."""

    def __init__(self) -> None:
        self._used_ids: dict[str, int] = {}

    def render(self, text: str) -> str:
        return "\n".join(self._blocks(self._lines(text)))

    # -- blocks ------------------------------------------------------------
    @staticmethod
    def _lines(text: str) -> list[str]:
        return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def _blocks(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue

            fence = FENCE_RE.match(line)
            if fence:
                marker, language = fence.group(1), fence.group(2)
                body: list[str] = []
                i += 1
                while i < len(lines) and not self._closes(lines[i], marker):
                    body.append(lines[i])
                    i += 1
                i += 1  # the closing fence (or EOF — the fence stays "open")
                code = html.escape("\n".join(body))
                cls = f' class="language-{html.escape(language)}"' if language else ""
                # No trailing newline: <pre> would render it as an extra blank line.
                out.append(f"<pre><code{cls}>{code}</code></pre>")
                continue

            heading = HEADING_RE.match(line)
            if heading:
                level = len(heading.group(1))
                inner = heading.group(2)
                anchor = self._heading_id(inner)
                out.append(f'<h{level} id="{anchor}">{self.inline(inner)}</h{level}>')
                i += 1
                continue

            if RULE_RE.match(line):
                out.append("<hr />")
                i += 1
                continue

            if QUOTE_RE.match(line):
                quote: list[str] = []
                while i < len(lines) and lines[i].strip():
                    match = QUOTE_RE.match(lines[i])
                    if match:
                        quote.append(match.group(1))
                    elif quote and not self._starts_block(lines[i]):
                        quote.append(lines[i])
                    else:
                        break
                    i += 1
                inner = "\n".join(self._blocks(quote))
                out.append(f"<blockquote>\n{inner}\n</blockquote>")
                continue

            if self._is_table(lines, i):
                table, i = self._table(lines, i)
                out.append(table)
                continue

            if LIST_RE.match(line):
                block: list[str] = []
                while i < len(lines) and lines[i].strip():
                    if LIST_RE.match(lines[i]):
                        block.append(lines[i])
                    elif self._indent(lines[i]) > self._indent(block[-1]):
                        block.append(lines[i])
                    elif not self._starts_block(lines[i]):
                        block.append(lines[i])  # lazy continuation of an item
                    else:
                        break
                    i += 1
                out.append(self._list(block))
                continue

            paragraph: list[str] = []
            while i < len(lines) and lines[i].strip() and not self._starts_block(lines[i]):
                paragraph.append(lines[i])
                i += 1
            if not paragraph:  # defensive: never loop forever
                paragraph.append(lines[i])
                i += 1
            out.append(f"<p>{self.inline(chr(10).join(paragraph))}</p>")
        return out

    def _starts_block(self, line: str) -> bool:
        return bool(
            FENCE_RE.match(line)
            or HEADING_RE.match(line)
            or RULE_RE.match(line)
            or QUOTE_RE.match(line)
            or LIST_RE.match(line)
        )

    @staticmethod
    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    @staticmethod
    def _closes(line: str, marker: str) -> bool:
        stripped = line.strip()
        return stripped and set(stripped) == {marker[0]} and len(stripped) >= len(marker)

    def _heading_id(self, text: str) -> str:
        slug = slugify(re.sub(r"[`*_~\[\]()]", "", text))
        seen = self._used_ids.get(slug, 0)
        self._used_ids[slug] = seen + 1
        return slug if seen == 0 else f"{slug}-{seen + 1}"

    # -- tables ------------------------------------------------------------
    @staticmethod
    def _split_row(line: str) -> list[str]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|") and not stripped.endswith("\\|"):
            stripped = stripped[:-1]
        cells = re.split(r"(?<!\\)\|", stripped)
        return [cell.strip().replace("\\|", "|") for cell in cells]

    def _is_table(self, lines: list[str], i: int) -> bool:
        if i + 1 >= len(lines) or "|" not in lines[i]:
            return False
        separator = lines[i + 1]
        if not TABLE_SEP_RE.match(separator) or "|" not in separator:
            return False
        return len(self._split_row(separator)) == len(self._split_row(lines[i]))

    def _table(self, lines: list[str], i: int) -> tuple[str, int]:
        header = self._split_row(lines[i])
        alignments = []
        for cell in self._split_row(lines[i + 1]):
            left, right = cell.startswith(":"), cell.endswith(":")
            alignments.append("center" if left and right else "right" if right else "left" if left else "")
        body: list[list[str]] = []
        i += 2
        while i < len(lines) and lines[i].strip() and "|" in lines[i]:
            body.append(self._split_row(lines[i])[: len(header)])
            i += 1
        style = lambda index: (  # noqa: E731 - terse local helper
            f' style="text-align:{alignments[index]}"'
            if index < len(alignments) and alignments[index] not in ("", "left")
            else ""
        )
        parts = ["<table>", "<thead>", "<tr>"]
        parts += [
            f"<th{style(index)}>{self.inline(cell)}</th>" for index, cell in enumerate(header)
        ]
        parts += ["</tr>", "</thead>", "<tbody>"]
        for row in body:
            parts.append("<tr>")
            parts += [
                f"<td{style(index)}>{self.inline(cell)}</td>" for index, cell in enumerate(row)
            ]
            parts.append("</tr>")
        parts += ["</tbody>", "</table>"]
        # Wrapped in a scrollable container: `overflow-x:auto` on a <table> is
        # unreliable across engines, so a wide table clips instead of scrolling.
        return '<div class="table-wrap">\n' + "\n".join(parts) + "\n</div>", i

    # -- lists -------------------------------------------------------------
    def _list(self, block: list[str]) -> str:
        items: list[tuple[int, bool, str]] = []
        for line in block:
            match = LIST_RE.match(line)
            if match:
                ordered = match.group(2)[-1] in ".)"
                text = match.group(4)
                if match.group("check") is not None:
                    box = "☒" if match.group("check").lower() == "x" else "☐"
                    text = f"{box} {text}"
                items.append((len(match.group(1)), ordered, text))
            elif items:
                indent, ordered, text = items[-1]
                items[-1] = (indent, ordered, f"{text}\n{line.strip()}")

        def render(start: int, indent: int) -> tuple[str, int]:
            ordered = items[start][1]
            tag = "ol" if ordered else "ul"
            parts: list[str] = []
            i = start
            while i < len(items) and items[i][0] >= indent:
                current_indent, _, text = items[i]
                if current_indent > indent:
                    nested, i = render(i, current_indent)
                    if parts and parts[-1].endswith("</li>"):
                        parts[-1] = parts[-1][: -len("</li>")] + nested + "</li>"
                    else:
                        parts.append(nested)
                    continue
                parts.append(f"<li>{self.inline(text)}</li>")
                i += 1
            return f"<{tag}>\n" + "\n".join(parts) + f"\n</{tag}>", i

        rendered, _ = render(0, items[0][0])
        return rendered

    # -- inline ------------------------------------------------------------
    def inline(self, text: str) -> str:
        escaped = html.escape(text, quote=True)
        rendered = INLINE_RE.sub(self._inline_sub, escaped)
        return re.sub(r" {2,}\n", "<br />\n", rendered)

    def _inline_sub(self, match: re.Match[str]) -> str:
        groups = match.groupdict()
        if groups["code_body"] is not None:
            return f"<code>{groups['code_body']}</code>"
        if groups["img_url"] is not None:
            title = f' title="{groups["img_title"]}"' if groups["img_title"] else ""
            return (
                f'<img src="{groups["img_url"]}" alt="{groups["img_alt"]}"'
                f"{title} loading=\"lazy\" />"
            )
        if groups["link_href"] is not None:
            title = f' title="{groups["link_title"]}"' if groups["link_title"] else ""
            href = groups["link_href"]
            external = href.startswith(("http://", "https://"))
            rel = ' rel="noopener"' if external else ""
            return f'<a href="{href}"{title}{rel}>{groups["link_text"]}</a>'
        if groups["strong"] is not None:
            return f"<strong>{groups['strong']}</strong>"
        if groups["strong_"] is not None and self._underscore_ok(match):
            return f"<strong>{groups['strong_']}</strong>"
        if groups["strike"] is not None:
            return f"<del>{groups['strike']}</del>"
        if groups["em"] is not None:
            return f"<em>{groups['em']}</em>"
        if groups["em_"] is not None and self._underscore_ok(match):
            return f"<em>{groups['em_']}</em>"
        return match.group(0)

    @staticmethod
    def _underscore_ok(match: re.Match[str]) -> bool:
        """`_em_` must sit on word boundaries, so snake_case stays literal.

        Checks the whole match — the delimiters included — not just the inner
        group: `snake_case_name` matched `_case_`, whose surrounding characters
        are letters on both sides.
        """
        start, end = match.start(), match.end()
        before = match.string[start - 1] if start else ""
        after = match.string[end] if end < len(match.string) else ""
        return not (before.isalnum() or after.isalnum())


def render_markdown(text: str) -> str:
    return MarkdownRenderer().render(text)


# ── Posts ───────────────────────────────────────────────────────────────────
FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")
SCALAR_LIST_RE = re.compile(r"^\[(.*)\]$")


class PostError(Exception):
    """A post file the build must reject (bad front matter, bad date, …)."""


@dataclass
class Post:
    slug: str
    title: str
    date: dt.date
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    author: str = ""
    draft: bool = False
    image: str = ""  # optional cover, e.g. assets/covers/foo.png (served at {base_url}/assets/…)
    body: str = ""
    source: str = ""  # repo-relative POSIX path, for the "edit this post" link
    source_name: str = ""  # filename, used as the same-date tiebreaker

    @property
    def url(self) -> str:
        return f"posts/{self.slug}.html"

    def tag_slugs(self) -> list[tuple[str, str]]:
        return [(tag, slugify(tag)) for tag in self.tags]


def _strip_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_front_matter(text: str, source: str) -> tuple[dict[str, object], str]:
    """Split `---` front matter from the body.

    A YAML *subset*: `key: value`, inline lists `[a, b]` and block lists. A
    syntax error is a hard failure — a silently ignored tag or date is worse
    than a red build.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, object] = {}
    pending_list: str | None = None
    index = 1
    while index < len(lines):
        line = lines[index]
        if line.strip() == "---":
            break
        if not line.strip():
            index += 1
            continue
        item = re.match(r"^\s*-\s+(.*)$", line)
        if item and pending_list:
            assert isinstance(meta[pending_list], list)
            meta[pending_list].append(_strip_scalar(item.group(1)))  # type: ignore[union-attr]
            index += 1
            continue
        entry = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not entry:
            raise PostError(f"{source}: cannot parse front matter line {index + 1}: {line!r}")
        key, value = entry.group(1), entry.group(2).strip()
        if not value:
            meta[key] = []
            pending_list = key
        else:
            inline = SCALAR_LIST_RE.match(value)
            if inline:
                meta[key] = [
                    _strip_scalar(part) for part in inline.group(1).split(",") if part.strip()
                ]
            else:
                meta[key] = _strip_scalar(value)
            pending_list = None
        index += 1
    else:
        raise PostError(f"{source}: front matter opened with --- but never closed")
    return meta, "\n".join(lines[index + 1 :]).lstrip("\n")


def parse_post(path: Path, blog_dir: Path) -> Post:
    text = path.read_text(encoding="utf-8")
    source = path.relative_to(REPO_ROOT).as_posix() if REPO_ROOT in path.parents else path.name
    meta, body = parse_front_matter(text, source)

    stem = path.stem
    filename_date = FILENAME_DATE_RE.match(stem)
    if filename_date:
        date_text, slug = filename_date.group(1), filename_date.group(2)
    else:
        date_text, slug = str(meta.get("date", "")), stem

    if not date_text:
        raise PostError(f"{source}: no date — name the file YYYY-MM-DD-slug.md or set `date:`")
    try:
        date = dt.date.fromisoformat(date_text)
    except ValueError as error:
        raise PostError(f"{source}: bad date {date_text!r} ({error})") from error

    title = str(meta.get("title", "")).strip()
    if not title:
        raise PostError(f"{source}: missing `title:` in front matter")

    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    summary = str(meta.get("summary", "")).strip() or excerpt(body)
    draft = str(meta.get("draft", "")).strip().lower() in ("true", "yes", "1")

    return Post(
        slug=slug,
        title=title,
        date=date,
        tags=[str(tag) for tag in tags],
        summary=summary,
        author=str(meta.get("author", "")).strip(),
        draft=draft,
        image=str(meta.get("image", "")).strip(),
        body=body,
        source=source,
        source_name=path.name,
    )


def load_posts(blog_dir: Path, include_drafts: bool = False, lang: str = "en") -> list[Post]:
    # English posts live in posts/; a translation lives in posts/<lang>/ and
    # shares its slug with the English original.
    posts_dir = blog_dir / "posts" if lang == "en" else blog_dir / "posts" / lang
    if not posts_dir.is_dir():
        if lang == "en":
            raise PostError(f"{posts_dir} does not exist")
        return []  # a language with no translations yet is simply empty
    posts = [parse_post(path, blog_dir) for path in sorted(posts_dir.glob("*.md"))]
    drafts = [post for post in posts if post.draft]
    if drafts and not include_drafts:
        print(f"  skipping {len(drafts)} draft post(s): {', '.join(p.slug for p in drafts)}")
    posts = [post for post in posts if include_drafts or not post.draft]
    slugs = [post.slug for post in posts]
    duplicates = {slug for slug in slugs if slugs.count(slug) > 1}
    if duplicates:
        raise PostError(f"duplicate post slug(s): {', '.join(sorted(duplicates))}")
    # Newest first by date. Within one date the filename breaks the tie (its
    # YYYY-MM-DD-slug form sorts the same way the files were named/added), so
    # the index reads as reverse order of addition rather than by title.
    posts.sort(key=lambda post: (post.date, post.source_name), reverse=True)
    return posts


BODY_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)")
# A post links to a sibling post as ./<slug>.html — both live under posts/, so
# the relative path is correct from any post page.
INTRA_POST_RE = re.compile(r"^\./([a-z0-9-]+)\.html$")


def validate_post_links(post: Post, known_slugs: set[str]) -> None:
    """Reject body links that resolve in the source tree but 404 once published.

    Only ``build/`` is served, so a relative link to a file outside the blog
    (``../notes/x.md``) resolves in the tree and still breaks on the site. Two
    relative forms are allowed: site assets (``../assets/…``, which resolve
    identically from ``blog/posts/`` in both trees) and links between published
    posts (``./posts/<slug>.html`` or ``posts/<slug>.html``), checked against
    the set of posts being built so a typo fails instead of 404ing.
    """
    for target in BODY_LINK_RE.findall(post.body):
        if target.startswith(("http://", "https://", "mailto:", "#", "//")):
            continue
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            continue
        path = parsed.path
        if path.startswith("assets/") or path.startswith("../assets/"):
            continue
        intra = INTRA_POST_RE.match(path)
        if intra:
            if intra.group(1) in known_slugs:
                continue
            raise PostError(
                f"{post.source}: link to post {intra.group(1)!r} has no published target — "
                "check the slug, or publish that post (drafts are not built)"
            )
        raise PostError(
            f"{post.source}: link target {target!r} resolves in the repository but not on the "
            "published site — use an absolute URL (e.g. the GitHub blob link), keep the file "
            "under blog/assets/ and link it as ../assets/<file>, or link a sibling post as ./<slug>.html"
        )


# ── HTML ────────────────────────────────────────────────────────────────────
# ── Internationalization ────────────────────────────────────────────────────
# English is the default site (served at /); Simplified Chinese is a mirror
# under /zh/. Posts share a slug across languages. UI chrome is localized via
# UI_STRINGS; body content comes from each language's own source files.
SUPPORTED_LANGS = ("en", "zh")
HTML_LANG = {"en": "en", "zh": "zh-CN"}
ALT_LANG = {"en": "zh-CN", "zh": "en"}
LANG_LABEL = {"en": "中文", "zh": "English"}  # the *other* language's label
UI_STRINGS = {
    "en": {
        "skip": "Skip to content",
        "nav_posts": "Posts",
        "nav_tags": "Tags",
        "theme_label": "Toggle color theme",
        "source_label": "Source:",
        "tags_heading": "Tags",
        "all_posts": "← All posts",
        "edit": "Edit this post",
        "no_posts": "No posts yet.",
        "no_tags": "No tags yet.",
    },
    "zh": {
        "skip": "跳到正文",
        "nav_posts": "文章",
        "nav_tags": "标签",
        "theme_label": "切换深浅色主题",
        "source_label": "源码：",
        "tags_heading": "标签",
        "all_posts": "← 全部文章",
        "edit": "编辑本文",
        "no_posts": "还没有文章。",
        "no_tags": "还没有标签。",
    },
}


def page(
    *,
    config: dict[str, object],
    title: str,
    body: str,
    depth: int,
    description: str = "",
    canonical: str | None = None,
    page_type: str = "website",
    image: str = "",
    lang: str = "en",
    alt_link: str | None = None,
    alt_href: str = "",
) -> str:
    root = "../" * depth
    site_title = html.escape(str(config["title"]))
    site_repo = html.escape(str(config.get("site_repo") or config["repo_url"]))
    full_title = site_title if title == site_title else f"{html.escape(title)} · {site_title}"
    ui = UI_STRINGS[lang]
    base = str(config["base_url"])
    alt = f'<link rel="alternate" hreflang="{ALT_LANG[lang]}" href="{alt_link}" />' if alt_link else ""
    xdefault = (
        f'<link rel="alternate" hreflang="x-default" href="{alt_link}" />'
        if (alt_link and lang == "zh")
        else ""
    )
    feed_href = f"{root}feed.xml" if lang == "en" else f"{root}zh/feed.xml"
    skip = ui["skip"]
    nav_posts = ui["nav_posts"]
    nav_tags = ui["nav_tags"]
    lang_switch = (
        f'<a class="lang-switch" href="{root}{alt_href}" hreflang="{ALT_LANG[lang]}">{LANG_LABEL[lang]}</a>'
        if alt_href
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="{HTML_LANG[lang]}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{full_title}</title>
<meta name="description" content="{html.escape(description or str(config['description']))}" />
<meta property="og:type" content="{page_type}" />
<meta property="og:title" content="{full_title}" />
<meta property="og:description" content="{html.escape(description or str(config['description']))}" />
{f'<meta property="og:image" content="{base}/{html.escape(image)}" /><meta name="twitter:card" content="summary_large_image" />' if image else ''}
{f'<link rel="canonical" href="{canonical}" />' if canonical else ''}
{alt}
{xdefault}
<link rel="alternate" type="application/rss+xml" title="{site_title}" href="{feed_href}" />
<link rel="stylesheet" href="{root}assets/blog.css" />
<script src="{root}assets/blog.js" defer></script>
</head>
<body>
<a class="skip-link" href="#main">{skip}</a>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="brand" href="{root}index.html">
      <span class="brand-mark">◆</span>
      <span class="brand-text">{site_title}</span>
    </a>
    <nav class="site-nav">
      <a href="{root}index.html">{nav_posts}</a>
      <a href="{root}tags/index.html">{nav_tags}</a>
      <a href="{feed_href}">RSS</a>
      <a class="nav-external" href="{site_repo}">GitHub</a>
      {lang_switch}
      <button class="theme-toggle" type="button" data-theme-toggle aria-label="{ui['theme_label']}">◐</button>
    </nav>
  </div>
</header>
<main id="main" class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p>{site_title} — {html.escape(str(config['tagline']))}.</p>
    <p class="muted">{ui['source_label']} <a href="{html.escape(str(config['repo_url']))}/tree/{html.escape(str(config['repo_branch']))}/blog">blog/</a> · <a href="{feed_href}">RSS</a></p>
  </div>
</footer>
</body>
</html>
"""


def post_card(post: Post, root: str) -> str:
    tags = "".join(
        f'<a class="tag" href="{root}tags/{slug}.html">{html.escape(tag)}</a>'
        for tag, slug in post.tag_slugs()
    )
    return f"""<article class="post-card">
  {f'<a class="post-card-cover" href="{root}{post.url}"><img src="{root}{html.escape(post.image)}" alt="" loading="lazy"></a>' if post.image else ''}
  <p class="post-meta"><time datetime="{post.date.isoformat()}">{post.date.isoformat()}</time></p>
  <h2><a href="{root}{post.url}">{html.escape(post.title)}</a></h2>
  <p class="post-summary">{html.escape(post.summary)}</p>
  {f'<p class="post-tags">{tags}</p>' if tags else ''}
</article>"""


def lang_config(config: dict[str, object], lang: str) -> dict[str, object]:
    """Config with localized chrome applied (title/tagline/description)."""
    if lang == "en":
        return config
    merged = dict(config)
    i18n = config.get("i18n", {})
    if isinstance(i18n, dict):
        for key, value in i18n.get(lang, {}).items():
            merged[key] = value
    return merged


def alt_for(lang: str, path: str, base: str) -> tuple[str, str]:
    """(absolute alt link, root-relative alt href) for `path` like 'posts/x.html'.

    English lives at /<path>; Chinese at /zh/<path>. The href is root-relative;
    page() prefixes the page's own ../ depth so it resolves from any depth.
    """
    alt_path = path if lang == "zh" else f"zh/{path}"
    return f"{base}/{alt_path}", alt_path


def render_index(
    config: dict[str, object],
    posts: list[Post],
    tags: list[tuple[str, str, int]],
    lang: str = "en",
) -> str:
    cfg = lang_config(config, lang)
    ui = UI_STRINGS[lang]
    base = str(config["base_url"]).rstrip("/")
    prefix = "" if lang == "en" else "zh/"
    # The English index sits at the site root (depth 0); the Chinese index sits
    # one level down under /zh/ (depth 1), so its relative links need "../".
    index_root = "" if lang == "en" else "../"
    cards = "\n".join(post_card(post, index_root) for post in posts) or (
        f'<p class="muted">{ui["no_posts"]}</p>'
    )
    tag_cloud = ""
    if tags:
        tag_cloud = (
            '<p class="tag-cloud">'
            + "".join(
                f'<a class="tag" href="{index_root}tags/{slug}.html">{html.escape(tag)} <span class="count">{count}</span></a>'
                for tag, slug, count in tags
            )
            + "</p>"
        )
    body = f"""<section class="hero">
  <h1>{html.escape(str(cfg['title']))}</h1>
  <p class="tagline">{html.escape(str(cfg['tagline']))}</p>
  {tag_cloud}
</section>
<section class="post-list">
{cards}
</section>"""
    alt_link, alt_href = alt_for(lang, "index.html", base)
    return page(
        config=cfg,
        title=str(cfg["title"]),
        body=body,
        depth=0 if lang == "en" else 1,
        canonical=f"{base}/{prefix}",
        lang=lang,
        alt_link=alt_link,
        alt_href=alt_href,
    )


def render_post(config: dict[str, object], post: Post, body_html: str, lang: str = "en") -> str:
    cfg = lang_config(config, lang)
    ui = UI_STRINGS[lang]
    base = str(config["base_url"]).rstrip("/")
    depth = 1 if lang == "en" else 2
    root = "../" * depth
    prefix = "" if lang == "en" else "zh/"
    # Body asset links are written as ../assets/… (correct from a depth-1
    # English post). A Chinese post sits one level deeper (/zh/posts/), so
    # those links need one more ../. Sibling-post links (./<slug>.html) stay
    # valid because both languages keep posts under their own posts/ dir.
    if lang != "en":
        body_html = body_html.replace('src="../assets/', 'src="../../assets/').replace(
            'href="../assets/', 'href="../../assets/'
        )
    tags = "".join(
        f'<a class="tag" href="{root}tags/{slug}.html">{html.escape(tag)}</a>'
        for tag, slug in post.tag_slugs()
    )
    edit = (
        f"{config['repo_url']}/edit/{config['repo_branch']}/{post.source}"
    )
    author = f'<span class="post-author">{html.escape(post.author)}</span>' if post.author else ""
    body = f"""<article class="post">
  <header class="post-header">
    <h1>{html.escape(post.title)}</h1>
    <p class="post-meta">
      <time datetime="{post.date.isoformat()}">{post.date.isoformat()}</time>
      {author}
    </p>
    {f'<p class="post-tags">{tags}</p>' if tags else ''}
  </header>
  <div class="prose">
{body_html}
  </div>
  <footer class="post-footer">
    <a href="{html.escape(edit)}">{ui['edit']}</a>
    <a href="{root}index.html">{ui['all_posts']}</a>
  </footer>
</article>"""
    alt_link, alt_href = alt_for(lang, post.url, base)
    return page(
        config=cfg,
        title=post.title,
        body=body,
        depth=depth,
        description=post.summary,
        canonical=f"{base}/{prefix}{post.url}",
        page_type="article",
        image=post.image,
        lang=lang,
        alt_link=alt_link,
        alt_href=alt_href,
    )


def render_tag_index(config: dict[str, object], tags: list[tuple[str, str, int]], lang: str = "en") -> str:
    cfg = lang_config(config, lang)
    ui = UI_STRINGS[lang]
    base = str(config["base_url"]).rstrip("/")
    prefix = "" if lang == "en" else "zh/"
    if tags:
        items = "\n".join(
            f'<li><a href="{slug}.html">{html.escape(tag)}</a> <span class="count">{count}</span></li>'
            for tag, slug, count in tags
        )
        listing = f'<ul class="tag-index">\n{items}\n</ul>'
    else:
        listing = f'<p class="muted">{ui["no_tags"]}</p>'
    body = f'<section class="hero"><h1>{ui["tags_heading"]}</h1></section>\n<section class="post-list">{listing}</section>'
    alt_link, alt_href = alt_for(lang, "tags/index.html", base)
    return page(
        config=cfg,
        title=ui["tags_heading"],
        body=body,
        depth=1 if lang == "en" else 2,
        canonical=f"{base}/{prefix}tags/",
        lang=lang,
        alt_link=alt_link,
        alt_href=alt_href,
    )


def render_tag_page(config: dict[str, object], tag: str, posts: list[Post], lang: str = "en") -> str:
    cfg = lang_config(config, lang)
    base = str(config["base_url"]).rstrip("/")
    prefix = "" if lang == "en" else "zh/"
    cards = "\n".join(post_card(post, "../") for post in posts)
    body = (
        f'<section class="hero"><h1>{html.escape(tag)}</h1>'
        f'<p class="muted">{len(posts)} post(s)</p>'
        f'<p><a href="index.html">← All tags</a></p></section>\n'
        f'<section class="post-list">\n{cards}\n</section>'
    )
    alt_link, alt_href = alt_for(lang, f"tags/{slugify(tag)}.html", base)
    return page(
        config=cfg,
        title=f"Tag: {tag}",
        body=body,
        depth=1 if lang == "en" else 2,
        canonical=f"{base}/{prefix}tags/{slugify(tag)}.html",
        lang=lang,
        alt_link=alt_link,
        alt_href=alt_href,
    )


def render_feed(config: dict[str, object], posts: list[Post], lang: str = "en") -> str:
    cfg = lang_config(config, lang)
    base = str(config["base_url"]).rstrip("/")
    prefix = "" if lang == "en" else "zh/"
    limit = int(config["feed_size"])
    items = []
    for post in posts[:limit]:
        published = email.utils.format_datetime(
            dt.datetime(post.date.year, post.date.month, post.date.day, tzinfo=dt.timezone.utc)
        )
        items.append(
            "\n".join(
                (
                    "  <item>",
                    f"    <title>{html.escape(post.title)}</title>",
                    f"    <link>{base}/{prefix}{post.url}</link>",
                    f'    <guid isPermaLink="true">{base}/{prefix}{post.url}</guid>',
                    f"    <pubDate>{published}</pubDate>",
                    f"    <description>{html.escape(post.summary)}</description>",
                    *(
                        [f"    <category>{html.escape(tag)}</category>" for tag in post.tags]
                    ),
                    "  </item>",
                )
            )
        )
    updated = posts[0].date.isoformat() if posts else dt.date.today().isoformat()
    feed_lang = "en" if lang == "en" else "zh-CN"
    return "\n".join(
        (
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
            "  <channel>",
            f"    <title>{html.escape(str(cfg['title']))}</title>",
            f"    <link>{base}/{prefix}</link>",
            f"    <description>{html.escape(str(cfg['description']))}</description>",
            f"    <language>{feed_lang}</language>",
            f"    <lastBuildDate>{updated}</lastBuildDate>",
            f'    <atom:link href="{base}/{prefix}feed.xml" rel="self" type="application/rss+xml" />',
            *items,
            "  </channel>",
            "</rss>",
            "",
        )
    )


def render_sitemap(config: dict[str, object], posts: list[Post], tags: list[tuple[str, str, int]]) -> str:
    base = str(config["base_url"]).rstrip("/")
    urls = [f"{base}/", f"{base}/tags/"]
    urls += [f"{base}/{post.url}" for post in posts]
    urls += [f"{base}/tags/{slug}.html" for _, slug, _ in tags]
    # Chinese mirror.
    urls += [f"{base}/zh/", f"{base}/zh/tags/"]
    urls += [f"{base}/zh/{post.url}" for post in posts]
    urls += [f"{base}/zh/tags/{slug}.html" for _, slug, _ in tags]
    entries = "\n".join(f"  <url><loc>{url}</loc></url>" for url in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n"
    )


# ── Build ───────────────────────────────────────────────────────────────────
def load_config(blog_dir: Path, base_url: str | None = None) -> dict[str, object]:
    config = dict(DEFAULT_CONFIG)
    config_path = blog_dir / "blog.json"
    if config_path.is_file():
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        unknown = set(loaded) - set(DEFAULT_CONFIG)
        if unknown:
            raise PostError(f"{config_path}: unknown config key(s): {', '.join(sorted(unknown))}")
        config.update(loaded)
    if base_url:
        config["base_url"] = base_url
    required = ("title", "description", "base_url")
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise PostError(f"blog config missing {', '.join(missing)}")
    return config


def build(blog_dir: Path, out_dir: Path, base_url: str | None = None, include_drafts: bool = False) -> int:
    config = load_config(blog_dir, base_url)
    en_posts = load_posts(blog_dir, include_drafts, lang="en")
    en_slugs = {post.slug for post in en_posts}
    for post in en_posts:
        validate_post_links(post, en_slugs)

    zh_posts = load_posts(blog_dir, include_drafts, lang="zh")
    zh_slugs = {post.slug for post in zh_posts}
    # A translation must mirror an existing English post (same slug); a
    # Chinese-only post would leave the language switcher pointing at a 404.
    orphans = sorted(zh_slugs - en_slugs)
    if orphans:
        raise PostError(
            f"Chinese post(s) with no English original (slug must match): {', '.join(orphans)}"
        )
    for post in zh_posts:
        validate_post_links(post, zh_slugs)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    assets = blog_dir / "assets"
    if assets.is_dir():
        shutil.copytree(assets, out_dir / "assets")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    def emit(lang: str, posts: list[Post], dest: Path) -> int:
        counts: dict[str, int] = {}
        tags: dict[str, list[Post]] = {}
        for post in posts:
            for tag in post.tags:
                tags.setdefault(tag, []).append(post)
                counts[tag] = counts.get(tag, 0) + 1
        tag_list = sorted(
            ((tag, slugify(tag), counts[tag]) for tag in counts), key=lambda item: item[0].lower()
        )

        dest.mkdir(parents=True, exist_ok=True)
        (dest / "index.html").write_text(
            render_index(config, posts, tag_list, lang), encoding="utf-8"
        )
        (dest / "tags").mkdir(exist_ok=True)
        (dest / "tags" / "index.html").write_text(
            render_tag_index(config, tag_list, lang), encoding="utf-8"
        )
        for tag, slug, _ in tag_list:
            (dest / "tags" / f"{slug}.html").write_text(
                render_tag_page(config, tag, tags[tag], lang), encoding="utf-8"
            )
        (dest / "posts").mkdir(exist_ok=True)
        for post in posts:
            (dest / "posts" / f"{post.slug}.html").write_text(
                render_post(config, post, render_markdown(post.body), lang), encoding="utf-8"
            )
        (dest / "feed.xml").write_text(render_feed(config, posts, lang), encoding="utf-8")
        return len(tag_list)

    en_tags = emit("en", en_posts, out_dir)
    zh_tags = emit("zh", zh_posts, out_dir / "zh") if zh_posts else 0

    # The sitemap covers every URL in both languages (English slugs are the
    # superset, so reuse them for the /zh/ entries).
    all_tags = sorted(
        {slugify(tag) for post in en_posts for tag in post.tags}
        | {slugify(tag) for post in zh_posts for tag in post.tags}
    )
    (out_dir / "sitemap.xml").write_text(
        render_sitemap(config, en_posts, [(t, t, 0) for t in all_tags]), encoding="utf-8"
    )

    print(
        f"Built {len(en_posts)} en + {len(zh_posts)} zh post(s), {en_tags} en + {zh_tags} zh tag(s)"
        f" → {out_dir} (base_url {config['base_url']})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blog-dir", type=Path, default=DEFAULT_BLOG_DIR, help="blog source (default: blog/)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="output directory (default: build/blog)")
    parser.add_argument("--base-url", help="override blog.json base_url (feed/sitemap/canonical only)")
    parser.add_argument("--include-drafts", action="store_true", help="build posts marked draft: true")
    args = parser.parse_args(argv)
    try:
        return build(args.blog_dir.resolve(), args.out.resolve(), args.base_url, args.include_drafts)
    except PostError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
