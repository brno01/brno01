"""README rendering: structure, accessibility and the subset of HTML that GitHub accepts."""
from html.parser import HTMLParser

import pytest

ALLOWED_TAGS = {"p", "a", "picture", "source", "img", "kbd", "details", "summary", "b", "sub", "br"}
ALLOWED_ATTRS = {"href", "media", "srcset", "src", "width", "alt"}
VOID = {"source", "img", "br"}


class Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.imgs, self.pictures, self.srcs, self.errors = [], [], [], [], []
        self.stack, self.in_details_text, self.details_depth = [], [], 0
        self.text_in_details = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        for k in dict(attrs):
            if k not in ALLOWED_ATTRS:
                self.errors.append(f"attribute {k} on <{tag}>")
        if tag not in ALLOWED_TAGS:
            self.errors.append(f"tag <{tag}>")
        if tag == "img":
            self.imgs.append(dict(attrs))
            self.srcs.append(dict(attrs).get("src", ""))
            if self.stack and self.stack[-1] == "picture":
                self.pictures[-1].append("img")
        if tag == "source":
            self.srcs.append(dict(attrs).get("srcset", ""))
            self.pictures[-1].append(dict(attrs))
        if tag == "picture":
            self.pictures.append([])
        if tag == "details":
            self.details_depth += 1
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        assert self.stack and self.stack[-1] == tag, f"unbalanced </{tag}> with {self.stack}"
        self.stack.pop()
        if tag == "details":
            self.details_depth -= 1

    def handle_data(self, data):
        if self.details_depth:
            self.text_in_details.append(data)


@pytest.fixture(scope="module")
def readme(build, content):
    return build.render_readme(content)


@pytest.fixture(scope="module")
def scan(readme):
    s = Scan()
    s.feed(readme)
    s.close()
    assert s.stack == []
    return s


def test_first_line_is_generated_notice(readme):
    first = readme.splitlines()[0]
    assert first.startswith("<!--") and first.endswith("-->")
    assert "gerado" in first and "tools/build.py" in first


def test_committed_readme_matches_render(readme, build):
    assert build.README_PATH.read_text(encoding="utf-8") == readme


def test_only_github_safe_html(scan, readme):
    assert scan.errors == []
    assert {t for t, _ in scan.tags} <= ALLOWED_TAGS
    for banned in ("<style", "<svg", "<script", "style=", "class="):
        assert banned not in readme


def test_every_img_has_alt_and_full_width(scan):
    assert len(scan.imgs) == 8  # hero + six projects + activity
    for img in scan.imgs:
        assert img.get("alt", "").strip()
        assert img.get("width") == "100%"


def test_picture_structure(scan):
    assert len(scan.pictures) == len(scan.imgs) == 8
    for pic in scan.pictures:
        sources = [x for x in pic if isinstance(x, dict)]
        assert len(sources) == 3
        assert [s["media"] for s in sources] == [
            "(max-width: 767px) and (prefers-color-scheme: dark)",
            "(max-width: 767px)",
            "(prefers-color-scheme: dark)",
        ]
        assert [s["srcset"].rsplit("-", 2)[1:] for s in sources] == [
            ["mobile", "dark.svg"], ["mobile", "light.svg"], ["desktop", "dark.svg"]]
        assert pic[-1] == "img"


def test_all_image_urls_use_assets_base(scan, content):
    base = content["assets_base"]
    assert base.startswith("https://")
    assert scan.srcs
    for src in scan.srcs:
        assert src.startswith(base), src


def test_referenced_files_equal_generated_files(scan, content, assets):
    base = content["assets_base"]
    referenced = {s[len(base):] for s in scan.srcs}
    assert referenced == set(assets)
    assert len(referenced) == 32


def test_english_block_inside_details(readme, content, scan):
    en = content["english"]
    assert scan.details_depth == 0
    start, end = readme.index("<details>"), readme.index("</details>")
    for needle in (en["summary"], en["heading"], en["lede"], en["closing"]):
        assert start < readme.index(needle, start) < end
    assert readme.count("<details>") == 1
    assert "<summary><b>" in readme[start:end]
    # no English text leaks outside the block
    assert en["lede"] not in readme[:start] + readme[end:]
    assert en["closing"] not in readme[:start] + readme[end:]


def test_content_is_html_escaped(build, content):
    evil = {**content, "lede": "a <b>x</b> & \"q\""}
    out = build.render_readme(evil)
    assert "a &lt;b&gt;x&lt;/b&gt; &amp; &quot;q&quot;" in out


def test_project_links_wrap_pictures(readme, content):
    for p in content["projects"]:
        assert f'<a href="{p["url"]}">' in readme
