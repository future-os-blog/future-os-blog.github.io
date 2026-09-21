"""Regression tests for scripts/build.py.

The blog generator is deliberately dependency-free, which means every piece of
it — front matter, the markdown subset, slugging, feed/sitemap output — is code
we own and have to lock down here. Three properties matter most:

1. **Markdown is a subset, not best effort.** Constructs outside it must come
   out literal and escaped; nothing may be silently reinterpreted.
2. **The published site is address-independent.** Internal links stay relative
   so the same build serves from a project path, a bare domain or a CNAME.
3. **A malformed post fails the build.** A typo must never drop a tag, a date
   or a whole post from the published site.

Runs with the stock python3: `python3 scripts/test_build.py`.
"""
import contextlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build.py"

spec = importlib.util.spec_from_file_location("blog_build", SCRIPT)
blog = importlib.util.module_from_spec(spec)
# Registered before exec: build.py uses `from __future__ import annotations`, and
# dataclass field resolution looks the module up in sys.modules by name.
sys.modules[spec.name] = blog
spec.loader.exec_module(blog)

CONFIG = {
    "title": "Test Blog",
    "tagline": "testing",
    "description": "A test blog.",
    "base_url": "https://example.test/blog",
    "author": "Tester",
    "repo_url": "https://github.com/example/repo",
    "repo_branch": "main",
    "feed_size": 20,
}


def make_blog(posts, config=None, assets=True, zh_posts=None):
    """Write a throwaway blog tree: `posts` maps filename → file contents."""
    root = Path(tempfile.mkdtemp(prefix="blog-build-"))
    (root / "posts").mkdir(parents=True)
    config = dict(CONFIG if config is None else config)
    (root / "blog.json").write_text(json.dumps(config), encoding="utf-8")
    if assets:
        (root / "assets").mkdir()
        (root / "assets" / "blog.css").write_text("body{}", encoding="utf-8")
    for name, content in posts.items():
        (root / "posts" / name).write_text(content, encoding="utf-8")
    if zh_posts:
        (root / "posts" / "zh").mkdir()
        for name, content in zh_posts.items():
            (root / "posts" / "zh" / name).write_text(content, encoding="utf-8")
    return root


def run_build(blog_dir, **kwargs):
    out = blog_dir.parent / (blog_dir.name + "-out")
    with contextlib.redirect_stdout(io.StringIO()):
        blog.build(blog_dir, out, **kwargs)
    return out


def post(title, tags="[x]", extra="", body="Body text."):
    return f"---\ntitle: {title}\ntags: {tags}\n{extra}---\n\n{body}\n"


def render(text):
    return blog.render_markdown(text)


class FrontMatterTests(unittest.TestCase):
    def test_scalars_lists_and_quotes(self):
        meta, body = blog.parse_front_matter(
            '---\ntitle: "Quoted: title"\ntags: [rust, design]\nauthor: T\ndraft: true\n---\n\nHello\n',
            "t.md",
        )
        self.assertEqual(meta["title"], "Quoted: title")
        self.assertEqual(meta["tags"], ["rust", "design"])
        self.assertEqual(meta["author"], "T")
        self.assertEqual(meta["draft"], "true")
        self.assertEqual(body, "Hello\n")

    def test_block_list_form(self):
        meta, _ = blog.parse_front_matter("---\ntags:\n  - rust\n  - loop\n---\n\nx\n", "t.md")
        self.assertEqual(meta["tags"], ["rust", "loop"])

    def test_absent_front_matter_is_not_an_error(self):
        meta, body = blog.parse_front_matter("# Just markdown\n", "t.md")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# Just markdown\n")

    def test_unclosed_front_matter_fails(self):
        with self.assertRaises(blog.PostError):
            blog.parse_front_matter("---\ntitle: x\n\nbody\n", "t.md")

    def test_unparsable_line_fails(self):
        with self.assertRaises(blog.PostError):
            blog.parse_front_matter("---\ntitle: x\nnonsense line\n---\n\nb\n", "t.md")


class PostParsingTests(unittest.TestCase):
    def test_date_and_slug_come_from_the_filename(self):
        root = make_blog({"2026-09-21-hello-world.md": post("Hello")})
        parsed = blog.parse_post(root / "posts" / "2026-09-21-hello-world.md", root)
        self.assertEqual(parsed.slug, "hello-world")
        self.assertEqual(parsed.date.isoformat(), "2026-09-21")

    def test_missing_title_fails(self):
        root = make_blog({"2026-09-21-x.md": "---\ntags: [a]\n---\n\nbody\n"})
        with self.assertRaises(blog.PostError):
            blog.parse_post(root / "posts" / "2026-09-21-x.md", root)

    def test_undated_filename_without_front_matter_date_fails(self):
        root = make_blog({"no-date.md": post("Undated")})
        with self.assertRaises(blog.PostError):
            blog.parse_post(root / "posts" / "no-date.md", root)

    def test_summary_falls_back_to_the_body(self):
        root = make_blog({"2026-09-21-x.md": post("T", body="## Heading\n\nReal summary text here.")})
        parsed = blog.parse_post(root / "posts" / "2026-09-21-x.md", root)
        self.assertEqual(parsed.summary, "Real summary text here.")

    def test_excerpt_truncates_on_a_word_boundary(self):
        text = blog.excerpt("word " * 100, limit=20)
        self.assertTrue(text.endswith("…"))
        self.assertLessEqual(len(text), 21)

    def test_duplicate_slug_fails_the_build(self):
        root = make_blog(
            {
                "2026-01-01-dup.md": post("A"),
                "2026-02-02-dup.md": post("B"),
            }
        )
        with self.assertRaises(blog.PostError):
            blog.load_posts(root)

    def test_index_orders_newest_first_by_date_then_filename(self):
        """Reverse order of addition: date desc, and within a date the filename."""
        root = make_blog(
            {
                # Same date: the title would sort B before A, but the filename
                # (addition order) must win, so zeta then alpha.
                "2026-01-01-alpha.md": post("Zeta title"),
                "2026-01-01-zeta.md": post("Alpha title"),
                "2026-03-03-newest.md": post("Newest"),
            }
        )
        out = run_build(root)
        index = (out / "index.html").read_text(encoding="utf-8")
        positions = [index.index("Newest"), index.index("Alpha title"), index.index("Zeta title")]
        self.assertEqual(positions, sorted(positions))

    def test_repository_relative_links_are_rejected(self):
        """They resolve in the tree and 404 on the site — fail the build instead."""
        root = make_blog(
            {"2026-01-01-a.md": post("A", body="See [the RPC doc](../../architecture/rpc.md).")}
        )
        parsed = blog.parse_post(root / "posts" / "2026-01-01-a.md", root)
        with self.assertRaises(blog.PostError):
            blog.validate_post_links(parsed, {"a"})

    def test_absolute_and_asset_links_are_allowed(self):
        root = make_blog(
            {
                "2026-01-01-a.md": post(
                    "A",
                    body=(
                        "[repo](https://github.com/o/r/blob/main/docs/a.md) "
                        "![shot](../assets/shot.png) [top](#read-more)"
                    ),
                )
            }
        )
        parsed = blog.parse_post(root / "posts" / "2026-01-01-a.md", root)
        blog.validate_post_links(parsed, {"a"})

    def test_intra_post_links_are_checked_against_published_slugs(self):
        root = make_blog(
            {
                "2026-01-01-a.md": post("A", body="See [b](./b.html)."),
                "2026-01-02-b.md": post("B"),
            }
        )
        parsed = blog.parse_post(root / "posts" / "2026-01-01-a.md", root)
        # b is published — a ./b.html sibling link resolves
        blog.validate_post_links(parsed, {"a", "b"})
        # a slug that is not being built must fail, not 404
        with self.assertRaises(blog.PostError):
            blog.validate_post_links(parsed, {"a"})


class MarkdownTests(unittest.TestCase):
    def test_headings_get_unique_ids(self):
        html = render("# Title\n\n## Same\n\n## Same\n")
        self.assertIn('<h1 id="title">Title</h1>', html)
        self.assertIn('<h2 id="same">Same</h2>', html)
        self.assertIn('<h2 id="same-2">Same</h2>', html)

    def test_fenced_code_is_escaped_and_labelled(self):
        html = render("```rust\nlet x = <T>;\n```\n")
        self.assertIn('<pre><code class="language-rust">', html)
        self.assertIn("let x = &lt;T&gt;;", html)

    def test_code_fence_content_is_never_interpreted(self):
        html = render("```\n**not bold** [not](a-link)\n```\n")
        self.assertIn("**not bold** [not](a-link)", html)
        self.assertNotIn("<strong>", html)
        self.assertNotIn("<a ", html)

    def test_raw_html_is_escaped(self):
        html = render("<script>alert(1)</script>\n")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_inline_spans(self):
        html = render("`code` and **bold** and *italic* and ~~gone~~ and _under_\n")
        self.assertIn("<code>code</code>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>italic</em>", html)
        self.assertIn("<del>gone</del>", html)
        self.assertIn("<em>under</em>", html)

    def test_code_nested_in_emphasis_is_rendered(self):
        """**`summarized`** is how an arm name is emphasised in a table cell."""
        html = render("**`summarized`** (ours, default)\n")
        self.assertIn("<strong><code>summarized</code></strong> (ours, default)", html)
        self.assertNotIn("`", html)
        self.assertIn("<em><code>x</code></em>", render("*`x`*\n"))

    def test_code_span_inside_link_text_is_rendered_not_literal(self):
        """[`path`](url) is how a repo path is cited. The backticks must become a
        <code> element, not be emitted literally into the page."""
        html = render("see [`future.proto`](https://example.test/a/b.proto) here\n")
        self.assertIn(
            '<a href="https://example.test/a/b.proto" rel="noopener"><code>future.proto</code></a>',
            html,
        )
        self.assertNotIn("`", html)

    def test_plain_and_code_link_text_both_work(self):
        html = render("[plain](https://a.test) and [`code`](https://b.test)\n")
        self.assertIn('<a href="https://a.test" rel="noopener">plain</a>', html)
        self.assertIn('<a href="https://b.test" rel="noopener"><code>code</code></a>', html)

    def test_long_code_token_gains_break_opportunities(self):
        """A wide path must be breakable at its separators, not pushed whole
        onto the next line (which leaves the line before it nearly empty)."""
        html = render("see `scripts/compaction_experiment/run.sh` now\n")
        self.assertIn("scripts/<wbr>compaction_<wbr>experiment/<wbr>run.sh", html)

    def test_long_plain_text_token_gains_break_opportunities(self):
        """Not every wide token is in backticks."""
        html = render("scanned for curl/wget/requests/urllib/socket keywords\n")
        self.assertIn("curl/<wbr>wget/<wbr>requests/<wbr>urllib/<wbr>socket", html)

    def test_short_tokens_and_numbers_are_left_alone(self):
        # Short code spans stay intact, and a sentence full of separators is not
        # shredded: numbers keep their decimal point and thousands comma.
        html = render("`short-id` then 93.7% and 212,911 tokens, e.g. fine.\n")
        self.assertIn("<code>short-id</code>", html)
        self.assertIn("93.7%", html)
        self.assertIn("212,911", html)
        self.assertNotIn("93.<wbr>", html)
        self.assertNotIn("212,<wbr>", html)

    def test_break_points_never_split_an_html_entity(self):
        html = render("a & b < c > d \"q\" 'r' in a fairly long line here\n")
        self.assertIn("&amp;", html)
        self.assertNotIn("&amp<wbr>", html)
        self.assertNotIn("&lt<wbr>", html)

    def test_snake_case_is_not_emphasis(self):
        self.assertIn("snake_case_name", render("A snake_case_name stays literal.\n"))

    def test_links_and_images(self):
        html = render("[doc](../a.md) ![alt](../assets/x.png)\n")
        self.assertIn('<a href="../a.md">doc</a>', html)
        self.assertIn('<img src="../assets/x.png" alt="alt"', html)

    def test_external_links_are_marked_noopener(self):
        self.assertIn('rel="noopener"', render("[site](https://example.com)\n"))

    def test_nested_and_ordered_lists(self):
        html = render("- a\n  - b\n- c\n\n1. first\n2. second\n")
        self.assertIn("<ul>", html)
        self.assertIn("<li>a<ul>", html.replace("\n", ""))
        self.assertIn("<ol>", html)
        self.assertIn("<li>first</li>", html)

    def test_table_with_alignment(self):
        html = render("| Left | Right |\n| :--- | ---: |\n| a | b |\n")
        self.assertIn("<th>Left</th>", html)
        self.assertIn('style="text-align:right"', html)
        self.assertIn("<td>a</td>", html)

    def test_tables_are_wrapped_in_a_scroll_container(self):
        """overflow-x:auto on <table> is unreliable; the wrapper scrolls instead."""
        html = render("| A | B |\n| --- | --- |\n| 1 | 2 |\n")
        self.assertIn('<div class="table-wrap">', html)
        self.assertIn("<table>", html)

    def test_blockquote_rule_and_hard_break(self):
        html = render("> quoted\n\n---\n\nline one  \nline two\n")
        self.assertIn("<blockquote>", html)
        self.assertIn("<hr />", html)
        self.assertIn("<br />", html)

    def test_leading_delimiter_paragraph_terminates(self):
        # Regression guard: a paragraph followed by a block type must not spin.
        html = render("text\n# heading\nmore\n")
        self.assertEqual(html.count("<p>"), 2)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.root = make_blog(
            {
                "2026-01-01-older-post.md": post("Older post", tags="[alpha]"),
                "2026-03-03-newer-post.md": post("Newer post", tags="[alpha, beta]"),
                "2026-04-04-draft.md": post("Draft post", extra="draft: true\n"),
            }
        )
        self.out = run_build(self.root)

    def test_index_lists_published_posts_newest_first(self):
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("Draft post", index)
        self.assertLess(index.index("Newer post"), index.index("Older post"))

    def test_pages_feed_sitemap_and_nojekyll_are_written(self):
        for name in ("index.html", "feed.xml", "sitemap.xml", ".nojekyll"):
            self.assertTrue((self.out / name).is_file(), name)
        self.assertTrue((self.out / "posts" / "newer-post.html").is_file())
        self.assertTrue((self.out / "tags" / "alpha.html").is_file())
        self.assertTrue((self.out / "assets" / "blog.css").is_file())

    def test_feed_and_sitemap_are_well_formed_xml(self):
        feed = ET.parse(self.out / "feed.xml").getroot()
        items = feed.findall("./channel/item")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].findtext("link"), "https://example.test/blog/posts/newer-post.html")
        ET.parse(self.out / "sitemap.xml")

    def test_drafts_can_be_built_explicitly(self):
        out = run_build(
            make_blog({"2026-04-04-draft.md": post("Draft post", extra="draft: true\n")}),
            include_drafts=True,
        )
        self.assertIn("Draft post", (out / "index.html").read_text(encoding="utf-8"))

    def test_internal_links_are_relative_so_the_site_is_address_independent(self):
        """No root-absolute href/src: the same build must work under any base path."""
        for page in self.out.rglob("*.html"):
            html = page.read_text(encoding="utf-8")
            for value in re.findall(r'(?:href|src)="([^"]+)"', html):
                with self.subTest(page=page.name, value=value):
                    if value.startswith(("http://", "https://", "mailto:", "#", "data:")):
                        continue
                    self.assertFalse(value.startswith("/"), f"root-absolute link: {value}")

    def test_base_url_override_changes_only_absolute_references(self):
        out = run_build(make_blog({"2026-01-01-a.md": post("A")}), base_url="https://future-os-blog.github.io")
        feed = (out / "feed.xml").read_text(encoding="utf-8")
        self.assertIn("https://future-os-blog.github.io/posts/a.html", feed)
        html = (out / "posts" / "a.html").read_text(encoding="utf-8")
        self.assertIn('href="../index.html"', html)

    def test_unknown_config_key_is_rejected(self):
        root = make_blog({"2026-01-01-a.md": post("A")}, config={**CONFIG, "typo": 1})
        with self.assertRaises(blog.PostError):
            blog.load_config(root)

    def test_site_repo_steers_only_the_nav_github_link(self):
        """Top GitHub link → site_repo; footer Source and Edit link → repo_url."""
        root = make_blog(
            {"2026-01-01-a.md": post("A")},
            config={**CONFIG, "site_repo": "https://github.com/org/product"},
        )
        out = run_build(root)
        html = (out / "posts" / "a.html").read_text(encoding="utf-8")
        # nav uses site_repo
        self.assertIn('<a class="nav-external" href="https://github.com/org/product">GitHub</a>', html)
        # No per-post "edit this post" link is rendered any more.
        self.assertNotIn("/edit/", html)
        # footer source link still targets the blog repo
        self.assertIn("https://github.com/example/repo/tree/main/blog", html)

    def test_site_repo_defaults_to_repo_url(self):
        out = run_build(make_blog({"2026-01-01-a.md": post("A")}))
        html = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            '<a class="nav-external" href="https://github.com/example/repo">GitHub</a>', html
        )


class ChineseMirrorTests(unittest.TestCase):
    """The /zh/ mirror: same slugs, localized chrome, hreflang, language switch."""

    def build(self, zh_posts=None, config=None):
        return run_build(
            make_blog(
                {
                    "2026-01-02-beta.md": post("Beta", body="Beta body."),
                    "2026-01-01-alpha.md": post("Alpha", body="Alpha body."),
                },
                config=config,
                zh_posts=zh_posts,
            )
        )

    def test_no_translation_means_no_zh_tree(self):
        out = self.build()
        self.assertFalse((out / "zh").exists())

    def test_zh_pages_are_written_with_localized_chrome(self):
        config = dict(CONFIG)
        config["i18n"] = {"zh": {"title": "测试博客", "tagline": "中文标语"}}
        out = self.build(
            zh_posts={"2026-01-02-beta.md": post("贝塔", body="正文。")},
            config=config,
        )
        index = (out / "zh" / "index.html").read_text(encoding="utf-8")
        post_html = (out / "zh" / "posts" / "beta.html").read_text(encoding="utf-8")
        self.assertIn('lang="zh-CN"', index)
        self.assertIn("测试博客", index)
        self.assertIn("文章", index)  # localized nav label
        # English original is untouched.
        en_index = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn('lang="en"', en_index)
        self.assertNotIn("测试博客", en_index)
        self.assertIn("贝塔", post_html)

    def test_language_switch_points_at_the_other_language(self):
        """Regression: the switch must resolve from the site root, not the
        language root, or a Chinese page links back to itself."""
        import posixpath

        alpha = post("Alpha", body="Body.")
        out = run_build(
            make_blog(
                {"2026-01-01-alpha.md": alpha},
                zh_posts={"2026-01-01-alpha.md": alpha},
            )
        )
        cases = [
            ("index.html", "zh/index.html"),
            ("posts/alpha.html", "zh/posts/alpha.html"),
            ("tags/index.html", "zh/tags/index.html"),
            ("zh/index.html", "index.html"),
            ("zh/posts/alpha.html", "posts/alpha.html"),
            ("zh/tags/index.html", "tags/index.html"),
        ]
        for served, expected in cases:
            html = (out / served).read_text(encoding="utf-8")
            match = re.search(r'<a class="lang-switch" href="([^"]+)"', html)
            self.assertIsNotNone(match, served)
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname("/" + served), match.group(1))
            )
            self.assertEqual(resolved, "/" + expected, f"on /{served}")

    def test_hreflang_alternate_points_across_languages(self):
        out = self.build(zh_posts={"2026-01-01-alpha.md": post("阿尔法", body="正文。")})
        en_post = (out / "posts" / "alpha.html").read_text(encoding="utf-8")
        zh_post = (out / "zh" / "posts" / "alpha.html").read_text(encoding="utf-8")
        # English points at the Chinese mirror; Chinese points back at English.
        self.assertIn('hreflang="zh-CN" href="https://example.test/blog/zh/posts/alpha.html"', en_post)
        self.assertIn('hreflang="en" href="https://example.test/blog/posts/alpha.html"', zh_post)
        self.assertIn('hreflang="x-default" href="https://example.test/blog/posts/alpha.html"', zh_post)
        # Visible switcher: en shows 中文, zh shows English.
        self.assertIn('class="lang-switch"', en_post)
        self.assertIn(">中文</a>", en_post)
        self.assertIn(">English</a>", zh_post)

    def test_zh_feed_is_localized_and_under_zh(self):
        out = self.build(zh_posts={"2026-01-01-alpha.md": post("阿尔法", body="正文。")})
        feed = (out / "zh" / "feed.xml").read_text(encoding="utf-8")
        self.assertIn("<language>zh-CN</language>", feed)
        self.assertIn("/zh/posts/alpha.html", feed)
        self.assertIn("阿尔法", feed)

    def test_orphan_chinese_slug_fails(self):
        with self.assertRaises(blog.PostError):
            self.build(zh_posts={"2026-01-03-ghost.md": post("幽灵", body="无英文原文。")})

    def test_sitemap_lists_both_languages(self):
        out = self.build(zh_posts={"2026-01-01-alpha.md": post("阿尔法", body="正文。")})
        sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://example.test/blog/posts/alpha.html", sitemap)
        self.assertIn("https://example.test/blog/zh/posts/alpha.html", sitemap)
        self.assertIn("https://example.test/blog/zh/", sitemap)

    def test_zh_index_cover_and_links_get_an_extra_dotdot(self):
        config = dict(CONFIG)
        config["i18n"] = {"zh": {"title": "测试博客"}}
        cover = post("Cover", extra="image: assets/covers/a.png\n")
        out = run_build(
            make_blog(
                {"2026-01-01-alpha.md": cover},
                config=config,
                zh_posts={"2026-01-01-alpha.md": cover},
            )
        )
        en_index = (out / "index.html").read_text(encoding="utf-8")
        zh_index = (out / "zh" / "index.html").read_text(encoding="utf-8")
        # Covers resolve from the *site* root...
        self.assertIn('src="assets/covers/a.png"', en_index)
        self.assertIn('src="../assets/covers/a.png"', zh_index)
        # ...but post and tag links resolve from the *language* root, so on the
        # Chinese index they stay bare (../posts/... would escape /zh/ and hit
        # the English article — the bug this locks against).
        self.assertIn('href="posts/alpha.html"', zh_index)
        self.assertNotIn('href="../posts/alpha.html"', zh_index)
        self.assertIn('href="tags/x.html"', zh_index)
        self.assertIn('href="index.html">文章</a>', zh_index)

    def test_zh_post_body_figures_get_an_extra_dotdot(self):
        alpha = post("阿尔法", body="![图](../assets/x/fig.png)\n\n[另一篇](./beta.html)")
        out = run_build(
            make_blog(
                {
                    "2026-01-01-alpha.md": alpha,
                    "2026-01-02-beta.md": post("贝塔", body="正文。"),
                },
                zh_posts={
                    "2026-01-01-alpha.md": alpha,
                    "2026-01-02-beta.md": post("贝塔", body="正文。"),
                },
            )
        )
        en_post = (out / "posts" / "alpha.html").read_text(encoding="utf-8")
        zh_post = (out / "zh" / "posts" / "alpha.html").read_text(encoding="utf-8")
        # English post at depth 1 keeps ../assets; Chinese at /zh/posts/ needs ../../.
        self.assertIn('src="../assets/x/fig.png"', en_post)
        self.assertIn('src="../../assets/x/fig.png"', zh_post)
        # Sibling-post links stay ./<slug>.html in both languages.
        self.assertIn('href="./beta.html"', en_post)
        self.assertIn('href="./beta.html"', zh_post)

    def test_zh_post_chrome_stays_inside_zh(self):
        """Tag links and "all posts" must not escape /zh/ to the English tree."""
        alpha = post("阿尔法", body="正文。")
        out = run_build(
            make_blog(
                {"2026-01-01-alpha.md": alpha},
                zh_posts={"2026-01-01-alpha.md": alpha},
            )
        )
        zh_post = (out / "zh" / "posts" / "alpha.html").read_text(encoding="utf-8")
        # lang_root for a /zh/posts/ page is "../" -> /zh/tags/, /zh/index.html.
        self.assertIn('class="tag" href="../tags/x.html"', zh_post)
        self.assertIn('href="../index.html">← 全部文章</a>', zh_post)
        self.assertNotIn('href="../../tags/', zh_post)
        self.assertNotIn('href="../../index.html"', zh_post)


class GiscusTests(unittest.TestCase):
    GISCUS = {
        "enabled": True,
        "repo": "o/r",
        "repo_id": "R_x",
        "category": "Announcements",
        "category_id": "DIC_x",
        "mapping": "pathname",
    }

    def config(self, **overrides):
        cfg = dict(CONFIG)
        cfg["giscus"] = {**self.GISCUS, **overrides}
        return cfg

    def test_disabled_by_default(self):
        out = run_build(make_blog({"2026-01-01-a.md": post("A")}))
        html = (out / "posts" / "a.html").read_text(encoding="utf-8")
        self.assertNotIn("giscus.app", html)

    def test_enabled_renders_giscus_with_real_attrs(self):
        out = run_build(make_blog({"2026-01-01-alpha.md": post("Alpha")}, config=self.config()))
        html = (out / "posts" / "alpha.html").read_text(encoding="utf-8")
        self.assertIn('src="https://giscus.app/client.js"', html)
        self.assertIn('data-repo="o/r"', html)
        self.assertIn('data-repo-id="R_x"', html)
        self.assertIn('data-category="Announcements"', html)
        self.assertIn('data-term="alpha"', html)
        self.assertIn('data-lang="en"', html)

    def test_en_and_zh_share_one_thread_and_zh_uses_zh_lang(self):
        alpha = post("Alpha", body="Body.")
        out = run_build(
            make_blog(
                {"2026-01-01-alpha.md": alpha},
                config=self.config(),
                zh_posts={"2026-01-01-alpha.md": alpha},
            )
        )
        en = (out / "posts" / "alpha.html").read_text(encoding="utf-8")
        zh = (out / "zh" / "posts" / "alpha.html").read_text(encoding="utf-8")
        # Both languages key the thread to the same slug.
        self.assertIn('data-term="alpha"', en)
        self.assertIn('data-term="alpha"', zh)
        self.assertIn('data-lang="en"', en)
        self.assertIn('data-lang="zh-CN"', zh)

    def test_enabled_with_missing_ids_fails(self):
        with self.assertRaises(blog.PostError):
            run_build(
                make_blog(
                    {"2026-01-01-a.md": post("A")},
                    config=self.config(repo_id=""),
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
