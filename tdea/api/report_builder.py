"""Report and export formatting: FR-41 and FR-46 CSV, FR-40 PDF (sprint 23 Oct).

Everything is built from the stored results (aggregator.results), never by running a
pipeline stage again (FR-42).

CSV: UTF-8 without BOM, LF line endings, RFC 4180 quoting, a header row with the
columns FR-41 and FR-46 name, floats in Python's shortest round-trip form and empty
cells for missing values, so the same stored results always give the same bytes
(NFR-06).

PDF (reportlab, A4): (a) repository metadata, (b) the Debt Score timeline drawn as a
vector line chart with the classification of each commit marked, (c) the top-10
high-risk file table, (d) mean, minimum, maximum and standard deviation of each metric,
(e) the detected debt patterns with their commit ranges and (f) a methodology note
naming the tools, versions and weights. reportlab's invariant mode fixes the creation
date and document ID, so the same stored results give the same PDF bytes.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from reportlab import rl_config
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from tdea import __version__
from tdea.aggregator import results
from tdea.aggregator.product_store import ProductStore
from tdea.aggregator.scoring import (DROP, SPIKE, SPIKE_THRESHOLD, TREND_DEAD_BAND,
                                     WEIGHTS)

CLASS_COLOURS = {SPIKE: colors.HexColor("#c0392b"), DROP: colors.HexColor("#27ae60"),
                 "Stable": colors.HexColor("#7f8c8d")}


def metrics_csv(rows: Iterable[Sequence[object]]) -> str:
    """Return FR-41's per-commit CSV."""
    return _csv(results.CSV_COLUMNS, rows)


def comparison_csv(rows: Iterable[Sequence[object]]) -> str:
    """Return FR-46's comparison CSV."""
    return _csv(results.COMPARISON_COLUMNS, rows)


def _csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """Return RFC 4180 CSV with LF record separators."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if value is None else repr(value) if isinstance(value, float)
                         else value for value in row])
    return buffer.getvalue()


def pdf_report(store: ProductStore, repository_id: int) -> bytes:
    """Return the FR-40 PDF report of one analysed repository."""
    card = results.overview(store, repository_id)
    points = results.series(store, repository_id)
    risky = results.high_risk(store, repository_id)
    stats = results.statistics(store, repository_id)
    found = results.patterns(store, repository_id)
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10)
    story: list = [Paragraph(f"TDEA report: {_esc(card['owner'])}/{_esc(card['name'])}",
                             styles["Title"])]
    story += [Paragraph("Repository", styles["Heading2"]), _table([
        ["URL", card["url"]], ["Commits in history", card["commits_total"]],
        ["Commits analysed", card["commits_analysed"]],
        ["Sampling interval N", card["sampling_n"]],
        ["Per-file analysis errors", card["file_errors"]],
        ["Analysis completed", card["last_completed_at"]]], header=False)]
    story += [Paragraph("Debt Score timeline", styles["Heading2"]), _timeline(points),
              Paragraph("Markers: red = Debt Spike, green = Refactoring Drop, grey = Stable. "
                        "x-axis: commit sequence (1 = oldest analysed commit).", body)]
    story += [Paragraph("Top 10 high-risk files", styles["Heading2"]), _table(
        [["File", "Mean CC", "Mean smell\ndensity", "Mean MI", "Dominant metric", "Risk"]]
        + [[Paragraph(_esc(row["path"]), cell), f"{row['mean_cc']:.2f}",
            f"{row['mean_smell_density']:.4f}", f"{row['mean_mi']:.1f}",
            row["dominant_metric"], row["risk_class"]] for row in risky],
        widths=[6.5 * cm, 1.8 * cm, 2.6 * cm, 1.7 * cm, 3.2 * cm, 1.6 * cm])]
    story += [Paragraph("Metric statistics across analysed commits", styles["Heading2"]),
              _table([["Metric", "Mean", "Minimum", "Maximum", "Std. deviation"]]
                     + [[row["metric"], f"{row['mean']:.3f}", f"{row['min']:.3f}",
                         f"{row['max']:.3f}", f"{row['std']:.3f}"] for row in stats])]
    story.append(Paragraph("Debt patterns", styles["Heading2"]))
    if found:
        story.append(_table([["Pattern", "Commits (sequence)", "First commit", "Last commit"]]
                            + [[row["label"],
                                f"{row['first_sequence']}-{row['last_sequence']} "
                                f"({row['length']})", row["first_sha"][:12],
                                row["last_sha"][:12]] for row in found]))
    else:
        story.append(Paragraph("No Steady Growth, Refactoring Event or Stable Phase pattern "
                               "was found.", body))
    story += [Paragraph("Methodology", styles["Heading2"]),
              Paragraph(_methodology(), body)]
    rl_config.invariant = 1
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, title=f"TDEA report {card['name']}",
                                 author=f"TDEA {__version__}", leftMargin=1.6 * cm,
                                 rightMargin=1.6 * cm, topMargin=1.6 * cm,
                                 bottomMargin=1.6 * cm)
    document.build(story)
    return buffer.getvalue()


def _methodology() -> str:
    """Return the methodology note (FR-40 f)."""
    return (f"TDEA {__version__} measured every Python file of each analysed commit with "
            "radon 6.0.1 (raw LOC/SLOC, cyclomatic complexity per function and method, "
            "Maintainability Index) and Pylint 4.1.1 with its default configuration "
            "(messages counted as C, R, W and E). Smell density = Pylint messages / SLOC. "
            "Each metric is min-max normalised across the repository's analysed commits "
            "(a constant metric normalises to 0). Debt Score = 100 x ("
            f"{WEIGHTS['cc']:.2f} x CC_norm + {WEIGHTS['smells']:.2f} x Smells_norm + "
            f"{WEIGHTS['mi']:.2f} x (1 - MI_norm) + {WEIGHTS['sloc_growth']:.2f} x "
            "SLOC_growth_norm), where Smells_norm is the normalised mean smell density and "
            "SLOC_growth the SLOC change from the previous analysed commit (0 for the "
            f"first). A commit is a Debt Spike when its delta exceeds +{SPIKE_THRESHOLD}, a "
            f"Refactoring Drop below -{SPIKE_THRESHOLD}, otherwise Stable. High-risk files "
            "are ranked by the mean of their normalised mean CC and normalised mean smell "
            "density; risk classes are terciles. The overall trend uses the least-squares "
            f"slope of the Debt Score with a dead band of {TREND_DEAD_BAND} points per "
            "commit. The normalisation, Smells_norm, SLOC_growth, ranking and trend rules "
            "follow the SRS open-issue proposals OI-01 to OI-05, which await the Product "
            "Owner's decision.")


def _timeline(points: Sequence[dict]) -> Drawing:
    """Return the Debt Score line chart with classification markers (FR-40 b)."""
    drawing = Drawing(17 * cm, 7 * cm)
    plot = LinePlot()
    plot.x, plot.y, plot.width, plot.height = 40, 30, 17 * cm - 60, 7 * cm - 50
    data = [(point["sequence"], point["debt_score"]) for point in points] or [(1, 0.0)]
    plot.data = [data]
    plot.lines[0].strokeColor = colors.HexColor("#2c3e50")
    last = max(2, max(sequence for sequence, _score in data))
    plot.xValueAxis.valueMin, plot.xValueAxis.valueMax = 1, last
    plot.yValueAxis.valueMin, plot.yValueAxis.valueMax = 0, 100
    plot.yValueAxis.valueStep = 20
    drawing.add(plot)
    for point in points:
        marker = makeMarker("FilledCircle")
        marker.size = 4
        marker.fillColor = CLASS_COLOURS.get(point["classification"], colors.grey)
        marker.x = plot.x + (point["sequence"] - 1) / (last - 1) * plot.width
        marker.y = plot.y + point["debt_score"] / 100 * plot.height
        drawing.add(marker)
    drawing.add(String(0, 7 * cm - 12, "Debt Score (0-100) per analysed commit",
                       fontSize=9))
    return drawing


def _table(rows: list[list], header: bool = True, widths: list | None = None) -> Table:
    """Return a styled table."""
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
             ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ecf0f1")),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    table.setStyle(TableStyle(style))
    return table


def _esc(text: object) -> str:
    """Escape text for a reportlab Paragraph."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
