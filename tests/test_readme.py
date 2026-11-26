"""README rendering: structure, accessibility and the subset of HTML that GitHub accepts."""
import re
from html.parser import HTMLParser

import pytest

ALLOWED_TAGS = {"p", "a", "picture", "source", "img", "kbd", "details", "summary", "b", "sub", "br"}
ALLOWED_ATTRS = {"href", "media", "srcset", "src", "width", "height", "alt", "align"}
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


def _art_imgs(scan):
    return [i for i in scan.imgs if i.get("width") == "100%"]


def _chip_imgs(scan):
    return [i for i in scan.imgs if i.get("width") == "11"]


def test_every_img_has_alt_and_a_known_size(scan):
    assert len(scan.imgs) == 7 + 4 + 15  # hero + six projects, one icon per contact and per stack chip
    assert len(_art_imgs(scan)) == 7 and len(_chip_imgs(scan)) == 19
    for img in _art_imgs(scan):
        assert img.get("alt", "").strip()
    for img in _chip_imgs(scan):
        # decorative: the name follows the icon
        assert img.get("alt") == "" and img.get("height") == "11" and img.get("align") == "top"


def test_picture_structure(scan):
    assert len(scan.pictures) == len(scan.imgs) == 26
    art = [p for p in scan.pictures if len([x for x in p if isinstance(x, dict)]) == 3]
    chips = [p for p in scan.pictures if len([x for x in p if isinstance(x, dict)]) == 1]
    assert len(art) == 7 and len(chips) == 19
    for pic in art:
        sources = [x for x in pic if isinstance(x, dict)]
        assert [s["media"] for s in sources] == [
            "(max-width: 767px) and (prefers-color-scheme: dark)",
            "(max-width: 767px)",
            "(prefers-color-scheme: dark)",
        ]
        assert [s["srcset"].rsplit("-", 2)[1:] for s in sources] == [
            ["mobile", "dark.svg"], ["mobile", "light.svg"], ["desktop", "dark.svg"]]
        assert pic[-1] == "img"
    for pic in chips:
        (source,) = [x for x in pic if isinstance(x, dict)]
        assert source["media"] == "(prefers-color-scheme: dark)" and source["srcset"].endswith("-dark.svg")
        assert pic[-1] == "img" and pic.index("img") > pic.index(source)  # <source> comes before <img>


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
    assert len(referenced) == 66


def test_chip_markup_per_item(readme, content):
    base = content["assets_base"].rstrip("/")
    n = 0
    for gr in content["stack"]:
        for it in gr["items"]:
            n += 1
            chip = (f'<kbd><picture><source media="(prefers-color-scheme: dark)" srcset="{base}/icon-{it["icon"]}-dark.svg">'
                    f'<img src="{base}/icon-{it["icon"]}-light.svg" width="11" height="11" alt="" align="top"></picture> '
                    f'{it["name"]}</kbd>')
            assert chip in readme, it["name"]
    assert n == 15
    assert readme.count("<kbd><picture>") == 15 + len(content["contacts"])


def test_contacts_are_kbd_links_with_an_icon_and_no_arrow(readme, content):
    assert "↗" not in readme
    base = content["assets_base"].rstrip("/")
    labels = [c["label"] for c in content["contacts"]]
    assert labels == ["LinkedIn", "Repositórios", "E-mail", "Discord"]
    for c in content["contacts"]:
        chip = (f'<a href="{c["url"]}"><kbd><picture><source media="(prefers-color-scheme: dark)" '
                f'srcset="{base}/icon-{c["icon"]}-dark.svg"><img src="{base}/icon-{c["icon"]}-light.svg" '
                f'width="11" height="11" alt="" align="top"></picture> {c["label"]}</kbd></a>')
        assert chip in readme, c["label"]


def test_section_order_and_no_activity_section(readme, content):
    lab = content["labels"]
    marks = [readme.index(m) for m in (
        "<picture>", content["lede"], f'## {lab["about"]}', f'## {lab["projects"]}', f'**{lab["also_private"]}**',
        f'**{lab["studies"]}:**', f'## {lab["stack"]}', "<details>", "<sub>")]
    assert marks == sorted(marks)
    assert readme.index("<picture>") < readme.index(content["lede"])
    assert "Atividade" not in readme and "activity" not in readme.lower()


def test_projects_are_six_linked_pictures_in_one_paragraph(readme, content):
    start = readme.index(f'## {content["labels"]["projects"]}')
    block = readme[start:readme.index("**", start + 10)]
    assert block.count("<p>") == 1 and block.count("</p>") == 1
    assert block.count("<picture>") == 6 and block.count("<a href=") == 6


def test_hero_picture_is_in_a_paragraph(readme):
    assert readme.split("\n\n")[1].startswith("<p>\n<picture>")


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
