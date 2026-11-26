"""Render captioned videos/GIFs from captured executions, never invented outputs.

Requires the separate research environment, ffmpeg and DejaVu fonts. These are
scripted visual explanations of real traces, not recordings of a browser UI.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import textwrap
from pathlib import Path

import sqlglot
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/assets"
W, H, FPS, SECONDS = 1280, 720, 15, 7
BG, PANEL, BORDER = "#081120", "#101d30", "#26354b"
WHITE, MUTED, GREEN, RED, BLUE = "#edf5ff", "#a1b2c9", "#34d399", "#fb7185", "#7dd3fc"
FONTS = Path("/usr/share/fonts/truetype/dejavu")
KEYWORDS = re.compile(
    r"\b(SELECT|FROM|WHERE|AS|WITH|DELETE|RETURNING|ORDER|BY|LIMIT|OR|IN)\b", re.I
)


def font(size: int, *, mono: bool = False, bold: bool = False):
    name = "DejaVuSansMono" if mono else "DejaVuSans"
    return ImageFont.truetype(str(FONTS / f"{name}{'-Bold' if bold else ''}.ttf"), size)


def wrapped(draw, text, pos, *, size=20, width=48, color=WHITE, mono=False, spacing=30):
    x, y = pos
    for paragraph in str(text).splitlines():
        lines = textwrap.wrap(paragraph, width=width, replace_whitespace=False) or [""]
        for line in lines:
            draw.text((x, y), line, font=font(size, mono=mono), fill=color)
            y += spacing
    return y


def code(draw, text, pos, *, width=43, size=20):
    x, y = pos
    for original in text.splitlines():
        for line in textwrap.wrap(original, width=width, replace_whitespace=False) or [""]:
            cursor = x
            end = 0
            for match in KEYWORDS.finditer(line):
                prefix = line[end : match.start()]
                draw.text((cursor, y), prefix, font=font(size, mono=True), fill=WHITE)
                cursor += draw.textlength(prefix, font=font(size, mono=True))
                token = match.group()
                draw.text((cursor, y), token, font=font(size, mono=True, bold=True), fill=BLUE)
                cursor += draw.textlength(token, font=font(size, mono=True, bold=True))
                end = match.end()
            draw.text((cursor, y), line[end:], font=font(size, mono=True), fill=WHITE)
            y += size + 8
    return y


COPY = {
    "en": {
        "tag": "THE SQL FIREWALL FOR LLM APPS",
        "trace": "REAL GUARD RESULTS",
        "input": "SQL FROM THE MODEL",
        "result": "GUARD RESULT",
        "allowed": "ALLOWED",
        "blocked": "REJECTED",
        "rows": "ROWS RETURNED BY THE DEMO DATABASE",
        "nothing": "Denied SQL is never executed.",
        "context": "Authenticated tenant: 7   |   Row limit: 100",
        "footer": "PostgreSQL validation · accepted reads executed on a synthetic SQLite fixture",
        "titles": [
            "Read only your own invoices",
            "A write hidden inside a CTE",
            "A SELECT can expose private columns",
            "OR 1 = 1 stays inside the tenant boundary",
            "A dangerous word can be harmless text",
            "Missing user context fails closed",
        ],
        "notes": [
            "The guard injects tenant_id = 7 and LIMIT 100.",
            "The syntax tree contains DELETE. The guard rejects it.",
            "customers.email is hidden by the application's policy.",
            "Both returned invoices still belong to tenant 7.",
            "A string literal is data, not a DROP statement.",
            "A required row-filter value was not provided.",
        ],
    },
    "es": {
        "tag": "EL FIREWALL DE SQL PARA APPS CON IA",
        "trace": "RESULTADOS REALES DEL GUARD",
        "input": "SQL GENERADO POR EL MODELO",
        "result": "RESULTADO DEL GUARD",
        "allowed": "PERMITIDO",
        "blocked": "RECHAZADO",
        "rows": "FILAS DEVUELTAS POR LA BASE DE DEMOSTRACIÓN",
        "nothing": "El SQL rechazado nunca se ejecuta.",
        "context": "Empresa autenticada: 7   |   Límite de filas: 100",
        "footer": ("Validación PostgreSQL; lecturas permitidas en datos ficticios SQLite"),
        "titles": [
            "Consulta solo las facturas de tu empresa",
            "Un borrado escondido dentro de un CTE",
            "Un SELECT puede exponer columnas privadas",
            "OR 1 = 1 sigue dentro de tu empresa",
            "Una palabra peligrosa puede ser solo texto",
            "Sin contexto del usuario, se rechaza",
        ],
        "notes": [
            "El guard añade tenant_id = 7 y LIMIT 100.",
            "El árbol contiene DELETE. El guard lo rechaza.",
            "La política de la aplicación oculta customers.email.",
            "Las dos facturas devueltas siguen siendo de la empresa 7.",
            "Un literal de texto es un dato, no una sentencia DROP.",
            "Falta un valor que necesita el filtro por usuario.",
        ],
    },
}


def pretty(sql: str) -> str:
    compact = sqlglot.transpile(sql, read="postgres", write="postgres", pretty=False)[0]
    return re.sub(r"\s+(FROM|WHERE|ORDER BY|LIMIT|RETURNING)\s+", r"\n\1 ", compact)


def frame(trace, language: str, index: int, elapsed: float) -> Image.Image:
    words = COPY[language]
    case = trace["cases"][index]
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # Precise UI graphics, built from the captured SQL and decision fields.
    d.rounded_rectangle((40, 29, 82, 73), radius=12, fill="#103a36", outline=GREEN, width=2)
    d.line((51, 50, 59, 59, 72, 42), fill=GREEN, width=4)
    d.text((97, 27), "sqlguardllm", font=font(34, bold=True), fill=WHITE)
    d.text((99, 72), words["tag"], font=font(12, bold=True), fill=MUTED)
    label_font = font(12, bold=True)
    tw = d.textlength(words["trace"], font=label_font)
    d.rounded_rectangle((W - tw - 77, 36, W - 42, 69), radius=15, fill="#12352f")
    d.text((W - tw - 59, 44), words["trace"], font=label_font, fill=GREEN)
    d.text((42, 113), words["titles"][index], font=font(30, bold=True), fill=WHITE)
    context_label = words["context"]
    if case["id"] == "missing_context":
        context_label = (
            "User context: missing   |   Row limit: 100"
            if language == "en"
            else "Contexto del usuario: ausente   |   Límite de filas: 100"
        )
    d.text((44, 159), context_label, font=font(16), fill=MUTED)
    d.rounded_rectangle((42, 203, 612, 605), radius=16, fill=PANEL, outline=BORDER, width=2)
    d.rounded_rectangle((632, 203, 1238, 605), radius=16, fill=PANEL, outline=BORDER, width=2)
    d.text((66, 225), words["input"], font=font(13, bold=True), fill=MUTED)
    d.text((656, 225), words["result"], font=font(13, bold=True), fill=MUTED)
    sql = pretty(case["input"])
    reveal = min(1, max(0, elapsed / 1.2))
    code(d, sql[: math.ceil(len(sql) * reveal)], (66, 270), width=43, size=19)
    if elapsed >= 1.5:
        accent = GREEN if case["ok"] else RED
        label = words["allowed"] if case["ok"] else words["blocked"]
        d.rounded_rectangle(
            (1028, 217, 1215, 253), radius=12, fill="#153d34" if case["ok"] else "#412132"
        )
        d.text((1043, 226), label, font=font(13, bold=True), fill=accent)
        d.text(
            (657, 270),
            f"ok = {str(case['ok']).lower()}",
            font=font(20, mono=True, bold=True),
            fill=accent,
        )
        if case["ok"]:
            end_y = code(d, pretty(case["sql"]), (657, 311), width=46, size=17)
            if elapsed >= 3.0:
                y = end_y + 17
                d.line((657, y, 1212, y), fill=BORDER)
                d.text((657, y + 12), words["rows"], font=font(11, bold=True), fill=MUTED)
                shown = json.dumps(case["rows"], ensure_ascii=False)
                wrapped(
                    d, shown, (657, y + 36), size=17, width=48, color=GREEN, mono=True, spacing=24
                )
        else:
            d.text((657, 310), "sql = null", font=font(19, mono=True), fill=WHITE)
            violation = case["violations"][0]
            d.text((657, 359), violation["code"], font=font(20, mono=True, bold=True), fill=RED)
            wrapped(d, violation["message"], (657, 401), size=18, width=48, color=WHITE, spacing=27)
            d.text((657, 553), words["nothing"], font=font(16), fill=MUTED)
    d.text((44, 626), words["notes"][index], font=font(18), fill=WHITE)
    d.text((44, 665), words["footer"], font=font(12), fill=MUTED)
    for i in range(6):
        d.rounded_rectangle(
            (1060 + i * 27, 669, 1076 + i * 27, 674), radius=2, fill=GREEN if i == index else BORDER
        )
    d.rectangle((0, 711, W, 719), fill=PANEL)
    d.rectangle((0, 711, int(W * (index + min(elapsed / SECONDS, 1)) / 6), 719), fill=GREEN)
    return im


def timestamp(seconds: int) -> str:
    return f"00:{seconds // 60:02d}:{seconds % 60:02d}.000"


def main() -> None:
    for language in ("en", "es"):
        trace = json.loads((OUT / f"demo-trace.{language}.json").read_text())
        output = OUT / f"demo.{language}.mp4"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            f"{W}x{H}",
            "-framerate",
            str(FPS),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ]
        encoder = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        previews = []
        try:
            for index in range(6):
                for n in range(FPS * SECONDS):
                    picture = frame(trace, language, index, n / FPS)
                    encoder.stdin.write(picture.tobytes())
                previews.append(frame(trace, language, index, 5).resize((640, 360)))
                print(f"{language}: scene {index + 1}/6", flush=True)
        finally:
            encoder.stdin.close()
        if encoder.wait() != 0:
            raise RuntimeError(f"ffmpeg failed for {language}")
        frame(trace, language, 0, 5).save(OUT / f"demo-poster.{language}.png")
        montage = Image.new("RGB", (1280, 1080), BG)
        for i, preview in enumerate(previews):
            montage.paste(preview, ((i % 2) * 640, (i // 2) * 360))
        montage.save(OUT / f"demo-contact.{language}.png")
        filters = (
            "fps=6,scale=960:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=96[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=3"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-threads",
                "2",
                "-filter_complex_threads",
                "1",
                "-i",
                str(output),
                "-t",
                "21",
                "-filter_complex",
                filters,
                "-loop",
                "0",
                str(OUT / f"demo.{language}.gif"),
            ],
            check=True,
        )
        captions = ["WEBVTT", ""]
        for i, (title, note) in enumerate(
            zip(COPY[language]["titles"], COPY[language]["notes"], strict=True)
        ):
            captions.extend(
                (f"{timestamp(i * SECONDS)} --> {timestamp((i + 1) * SECONDS)}", title, note, "")
            )
        (OUT / f"demo.{language}.vtt").write_text("\n".join(captions), encoding="utf-8")
        print(f"Saved video, GIF, poster and captions: {language}", flush=True)


if __name__ == "__main__":
    main()
