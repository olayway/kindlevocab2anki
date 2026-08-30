#!/usr/bin/env python3
"""Render the Anki card templates to a standalone HTML file for previewing.

Reads the *real* CARD_CSS and LAYOUTS from kindle_anki.py, substitutes sample
data the same way Anki does (a small subset of Mustache), and writes a gallery
you can open in a browser. What you see here is what Anki produces.

Workflow:
    python preview_cards.py && open card_preview.html
Then edit CARD_CSS / the templates in kindle_anki.py, re-run, and refresh.
"""

from __future__ import annotations

import html
import re
import webbrowser
from pathlib import Path

import kindle_anki as ka

OUT = Path(__file__).with_name("card_preview.html")

# A few sample notes covering the interesting cases: full data, no example
# sentence, no translation, a long definition. Each dict maps field -> value.
SAMPLES: list[dict[str, str]] = [
    {
        "Word": "ephemeral",
        "Translation": "ulotny",
        "Definition": "Lasting for a very short time; fleeting.",
        "Sentence": "The _____ beauty of the cherry blossoms drew crowds each spring.",
        "SentenceFull": 'The <b class="target">ephemeral</b> beauty of the '
        "cherry blossoms drew crowds each spring.",
        "Source": "The Remains of the Day",
        "LookupDate": "2026-08-14",
        # Production pairs (native Polish → target English), focus/target bolded
        # exactly as Claude emits them. One pair is shown per calendar day.
        "ProdNative1": 'Piękno kwiatów wiśni jest <b class="focus">ulotne</b>.',
        "ProdTarget1": 'The beauty of cherry blossoms is <b class="target">ephemeral</b>.',
        "ProdNative2": 'Sława bywa <b class="focus">ulotna</b>.',
        "ProdTarget2": 'Fame can be <b class="target">ephemeral</b>.',
        "ProdNative3": 'To była tylko <b class="focus">ulotna</b> chwila.',
        "ProdTarget3": 'It was only an <b class="target">ephemeral</b> moment.',
    },
    {
        "Word": "gregarious",
        "Translation": "towarzyski",
        "Definition": "Fond of the company of others; sociable.",
        "Sentence": "",  # no example sentence -> section should collapse
        "SentenceFull": "",
        "Source": "A Little Life",
        "LookupDate": "2026-08-10",
        # Only one pair here -> the production card still works; the rotation
        # script just always lands on it.
        "ProdNative1": 'Mój brat jest bardzo <b class="focus">towarzyski</b>.',
        "ProdTarget1": 'My brother is very <b class="target">gregarious</b>.',
    },
    {
        "Word": "petrichor",
        "Translation": "",  # no translation -> section should collapse
        "Definition": "A pleasant smell frequently accompanying the first rain "
        "after a long period of warm, dry weather.",
        "Sentence": "The _____ rose from the pavement as the storm finally broke.",
        "SentenceFull": 'The <b class="target">petrichor</b> rose from the '
        "pavement as the storm finally broke.",
        "Source": "Klara and the Sun",
        "LookupDate": "2026-08-02",
    },
]


def render_mustache(template: str, fields: dict[str, str], front_rendered: str = "") -> str:
    """Mimic the slice of Anki/Mustache the templates use.

    Supports {{Field}}, {{#Field}}...{{/Field}} (show if non-empty),
    {{^Field}}...{{/Field}} (show if empty), and {{FrontSide}}.
    """
    out = template.replace("{{FrontSide}}", front_rendered)

    # Conditional sections. The production template nests sections (each pair's
    # {{#ProdNativeN}} sits inside the card-wide {{#ProdNative1}} gate), so a
    # single pass would leave the inner ones intact — re-run until stable.
    def section(match: re.Match) -> str:
        kind, name, body = match.group(1), match.group(2), match.group(3)
        present = bool(fields.get(name, "").strip())
        show = present if kind == "#" else not present
        return body if show else ""

    while True:
        new = re.sub(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", section, out, flags=re.DOTALL)
        if new == out:
            break
        out = new

    # Plain field substitutions (leave HTML in field values as-is, like Anki).
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: fields.get(m.group(1), ""), out)
    return out


def render_card(front_tpl: str, back_tpl: str, fields: dict[str, str]) -> tuple[str, str]:
    front = render_mustache(front_tpl, fields)
    back = render_mustache(back_tpl, fields, front_rendered=front)
    return front, back


PAGE_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #eceef1;
  color: #1a1a1a;
  padding: 2rem;
}
h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
.sub { color: #666; margin: 0 0 2rem; font-size: 0.9rem; }
h2 {
  font-size: 1rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: #888; margin: 2.5rem 0 1rem; border-bottom: 1px solid #d5d8dd;
  padding-bottom: 0.4rem;
}
.row { display: flex; flex-wrap: wrap; gap: 1.25rem; }
.card-wrap { flex: 1 1 320px; min-width: 300px; max-width: 460px; }
.side-label {
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em;
  color: #999; margin: 0 0 0.4rem; font-weight: 600;
}
/* .anki-frame emulates Anki's rendering surface. The .card rule that ships
   with the note type is injected verbatim inside it. */
.anki-frame {
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 6px 20px rgba(0,0,0,0.06);
  overflow: hidden;
  background: #fff;
}
.dark-toggle {
  position: fixed; top: 1.25rem; right: 1.5rem;
  padding: 0.5rem 0.9rem; border-radius: 8px; border: 1px solid #ccc;
  background: #fff; cursor: pointer; font-size: 0.85rem;
}
body.dark { background: #1c1d20; color: #e6e6e6; }
body.dark h2 { color: #999; border-color: #35373b; }
body.dark .anki-frame { box-shadow: 0 1px 3px rgba(0,0,0,0.4); }
"""


def _card_row(fields: dict[str, str], front: str, back: str) -> list[str]:
    row = ['<div class="row">']
    for label, body in (("Front", front), ("Back", back)):
        row.append(
            f'<div class="card-wrap"><p class="side-label">{label}'
            f' — {html.escape(fields["Word"])}</p>'
            f'<div class="anki-frame"><div class="card">{body}</div></div></div>'
        )
    row.append("</div>")
    return row


def build_html() -> str:
    parts: list[str] = []
    for layout_name, (front_tpl, back_tpl) in ka.LAYOUTS.items():
        parts.append(f"<h2>Recognition — {html.escape(layout_name)} layout</h2>")
        for fields in SAMPLES:
            front, back = render_card(front_tpl, back_tpl, fields)
            parts.extend(_card_row(fields, front, back))

    # The production card is layout-independent (always native → target) and only
    # exists for notes that carry pairs, so we skip samples with no ProdNative1 —
    # the same empty-card gate Anki applies via {{#ProdNative1}}.
    parts.append("<h2>Production — native → target</h2>")
    parts.append(
        '<p class="sub">One pair is shown per calendar day (rotated by an inline '
        "script); notes without production data render no card.</p>"
    )
    for fields in SAMPLES:
        if not fields.get("ProdNative1", "").strip():
            continue
        front, back = render_card(ka.PRODUCTION_FRONT, ka.PRODUCTION_BACK, fields)
        parts.extend(_card_row(fields, front, back))

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Anki card preview</title>
<style>{PAGE_CSS}
/* ---- CARD_CSS from kindle_anki.py (verbatim) ---- */
{ka.CARD_CSS}
</style></head>
<body>
<button class="dark-toggle" onclick="toggleDark()">Toggle night mode</button>
<script>
// Mirror Anki: night mode adds a `nightMode` class to each rendered card.
function toggleDark() {{
  const on = document.body.classList.toggle('dark');
  document.querySelectorAll('.card').forEach(c => c.classList.toggle('nightMode', on));
}}
</script>
<h1>Anki card preview</h1>
<p class="sub">Rendered from CARD_CSS, LAYOUTS, and the production template in kindle_anki.py. Edit those, re-run, refresh.</p>
{''.join(parts)}
</body></html>
"""


def main() -> None:
    OUT.write_text(build_html(), encoding="utf-8")
    print(f"Wrote {OUT}")
    webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
