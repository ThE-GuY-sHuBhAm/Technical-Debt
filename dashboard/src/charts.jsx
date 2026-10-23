// The trend charts of the dashboard (FR-31 to FR-35) and the comparison overlay (FR-45).
//
// Every chart has a title, axis titles with units, a legend for each series and a hover
// tooltip with the commit hash, the value, the author and the date (NFR-10). Charts zoom
// on the commit axis by dragging across it or by Ctrl + mouse wheel, and "Reset zoom"
// restores the full range (SRS 4.1). Clicking a point opens that commit's drill-down
// (FR-37) when the view passes onSelect.
import { useRef } from 'react';
import {
  BarElement, CategoryScale, Chart as ChartJS, Filler, Legend, LinearScale, LineElement,
  PointElement, Title, Tooltip,
} from 'chart.js';
import annotationPlugin from 'chartjs-plugin-annotation';
import zoomPlugin from 'chartjs-plugin-zoom';
import { Bar, Line } from 'react-chartjs-2';
import { useTheme } from './theme.js';
import { formatNumber, shortSha, utcDate } from './format.js';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Title,
  Tooltip, Legend, Filler, annotationPlugin, zoomPlugin);

const COMMIT_AXIS = 'Commit sequence (1 = oldest analysed commit)';
export const SPIKE = 'Debt Spike';
export const DROP = 'Refactoring Drop';
export const STABLE = 'Stable';

function baseOptions(theme, { title, yTitle, points, onSelect, yMin, yMax, stacked }) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'index', intersect: false },
    onClick: (event, _elements, chart) => {
      if (!onSelect || !points) return;
      const hits = chart.getElementsAtEventForMode(event, 'index', { intersect: false }, true);
      if (hits.length && points[hits[0].index]) onSelect(points[hits[0].index].sha);
    },
    onHover: (event, elements) => {
      if (onSelect && event.native?.target) {
        event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
      }
    },
    scales: {
      x: {
        stacked,
        title: { display: true, text: COMMIT_AXIS, color: theme.inkSecondary },
        ticks: { color: theme.muted, autoSkip: true, maxRotation: 0 },
        grid: { display: false },
        border: { color: theme.axis },
      },
      y: {
        stacked,
        min: yMin,
        max: yMax,
        beginAtZero: true,
        title: { display: true, text: yTitle, color: theme.inkSecondary },
        ticks: { color: theme.muted },
        grid: { color: theme.grid },
        border: { display: false },
      },
    },
    plugins: {
      title: { display: true, text: title, color: theme.ink, align: 'start',
        font: { size: 15, weight: '600' }, padding: { bottom: 8 } },
      legend: { position: 'top', align: 'start',
        labels: { color: theme.inkSecondary, usePointStyle: true, boxWidth: 10 } },
      tooltip: {
        filter: (item) => item.raw !== null && item.raw !== undefined,
        callbacks: points ? {
          title: (items) => {
            const point = points[items[0].dataIndex];
            return point ? `Commit ${point.sequence} · ${shortSha(point.sha)}` : '';
          },
          afterBody: (items) => {
            const point = points[items[0].dataIndex];
            return point ? [`Author: ${point.author}`, `Date: ${utcDate(point.timestamp)}`] : [];
          },
          footer: () => (onSelect ? 'Click for the per-file breakdown' : ''),
        } : {},
      },
      zoom: {
        zoom: { drag: { enabled: true, threshold: 12 }, wheel: { enabled: true, modifierKey: 'ctrl' },
          pinch: { enabled: true }, mode: 'x' },
        limits: { x: { minRange: 1 } },
      },
    },
  };
}

function ChartFrame({ label, chartRef, children, note }) {
  return (
    <figure className="chart" aria-label={label}>
      <div className="chart-canvas">{children}</div>
      <figcaption className="chart-tools">
        <span className="muted">{note ?? 'Drag across the chart or Ctrl + scroll to zoom.'}</span>
        <button type="button" className="ghost" onClick={() => chartRef.current?.resetZoom()}>
          Reset zoom
        </button>
      </figcaption>
    </figure>
  );
}

function lineDataset(theme, label, data, slot = 0) {
  return {
    label,
    data,
    borderColor: theme.series[slot],
    backgroundColor: theme.series[slot],
    borderWidth: 2,
    pointRadius: data.length > 60 ? 0 : 3,
    pointHoverRadius: 6,
    pointBackgroundColor: theme.series[slot],
    pointBorderColor: theme.surface,
    tension: 0,
    spanGaps: false,
  };
}

/** A single-series line chart of one per-commit value. */
function MetricLine({ points, title, yTitle, field, label, onSelect, yMin, yMax,
  annotations, digits = 2 }) {
  const theme = useTheme();
  const chartRef = useRef(null);
  const options = baseOptions(theme, { title, yTitle, points, onSelect, yMin, yMax });
  options.plugins.tooltip.callbacks.label = (item) => `${label}: ${formatNumber(item.raw, digits)}`;
  if (annotations) options.plugins.annotation = { annotations: annotations(theme) };
  const data = {
    labels: points.map((point) => point.sequence),
    datasets: [lineDataset(theme, label, points.map((point) => point[field]))],
  };
  return (
    <ChartFrame label={title} chartRef={chartRef}>
      <Line ref={chartRef} options={options} data={data} />
    </ChartFrame>
  );
}

/** FR-31: total SLOC per commit. */
export function SlocChart({ points, onSelect }) {
  return <MetricLine points={points} onSelect={onSelect} field="sloc" label="Total SLOC"
    digits={0} title="Lines of code over the commit history"
    yTitle="Source lines of code (SLOC)" />;
}

/** FR-32: mean cyclomatic complexity with the CC = 5 reference line. */
export function ComplexityChart({ points, onSelect }) {
  return <MetricLine points={points} onSelect={onSelect} field="mean_cc"
    label="Mean cyclomatic complexity" title="Cyclomatic complexity over the commit history"
    yTitle="Mean cyclomatic complexity (per function)"
    annotations={(theme) => ({
      rankA: {
        type: 'line', yMin: 5, yMax: 5, borderColor: theme.inkSecondary, borderWidth: 1.5,
        borderDash: [6, 4],
        label: { display: true, content: 'CC = 5 (upper bound of rank A)', position: 'end',
          backgroundColor: theme.surface, color: theme.inkSecondary, font: { size: 11 } },
      },
    })} />;
}

/** FR-34: mean Maintainability Index with the red MI < 25 zone. */
export function MaintainabilityChart({ points, onSelect }) {
  return <MetricLine points={points} onSelect={onSelect} field="mean_mi"
    label="Mean Maintainability Index" title="Maintainability Index over the commit history"
    yTitle="Mean Maintainability Index (0–100)" yMin={0} yMax={100}
    annotations={(theme) => ({
      unmaintainable: {
        type: 'box', yMin: 0, yMax: 25, backgroundColor: theme.zone, borderWidth: 0,
        label: { display: true, content: 'Unmaintainable (MI < 25)', position: 'start',
          color: theme.critical, font: { size: 11, weight: '600' } },
      },
    })} />;
}

const CATEGORIES = [
  ['convention', 'Convention (C)'], ['refactor', 'Refactor (R)'],
  ['warning', 'Warning (W)'], ['error', 'Error (E)'],
];

/** FR-33: Pylint messages per commit, stacked by category. */
export function SmellChart({ points, onSelect }) {
  const theme = useTheme();
  const chartRef = useRef(null);
  const title = 'Code smells over the commit history';
  const options = baseOptions(theme, { title, yTitle: 'Pylint messages (count)', points,
    onSelect, stacked: true });
  options.plugins.tooltip.callbacks.label = (item) => `${item.dataset.label}: ${item.raw}`;
  options.plugins.tooltip.callbacks.footer = (items) => {
    const point = points[items[0].dataIndex];
    return [`Total: ${point.total_smells}`, onSelect ? 'Click for the per-file breakdown' : ''];
  };
  const data = {
    labels: points.map((point) => point.sequence),
    datasets: CATEGORIES.map(([field, label], slot) => ({
      label,
      data: points.map((point) => point[field]),
      backgroundColor: theme.series[slot],
      borderColor: theme.surface,
      borderWidth: { top: 2 },
      borderSkipped: 'bottom',
      maxBarThickness: 28,
    })),
  };
  return (
    <ChartFrame label={title} chartRef={chartRef}>
      <Bar ref={chartRef} options={options} data={data} />
    </ChartFrame>
  );
}

function markerDataset(label, points, classification, colour, rotation, style, theme) {
  const radius = style === 'triangle' ? 8 : 5; // a triangle covers less area than a circle
  return {
    label,
    data: points.map((point) => (point.classification === classification ? point.debt_score : null)),
    showLine: false,
    pointStyle: style,
    rotation,
    pointRadius: radius,
    pointHoverRadius: radius + 2,
    pointBackgroundColor: colour,
    pointBorderColor: theme.surface,
    pointBorderWidth: 1.5,
    borderColor: colour,
    backgroundColor: colour,
  };
}

/** FR-35: the Debt Score timeline with a classification marker on every point. */
export function DebtScoreChart({ points, onSelect }) {
  const theme = useTheme();
  const chartRef = useRef(null);
  const title = 'Debt Score timeline';
  const options = baseOptions(theme, { title, yTitle: 'Debt Score (0–100)', points, onSelect,
    yMin: 0, yMax: 100 });
  options.plugins.tooltip.callbacks.label = (item) => {
    const point = points[item.dataIndex];
    if (item.datasetIndex === 0) {
      return `Debt Score: ${formatNumber(item.raw, 2)} (Δ ${formatNumber(point.debt_delta, 2, true)})`;
    }
    return `Classification: ${point.classification}`;
  };
  const line = lineDataset(theme, 'Debt Score', points.map((point) => point.debt_score));
  line.pointRadius = 0;
  line.pointHoverRadius = 0;
  const data = {
    labels: points.map((point) => point.sequence),
    datasets: [
      line,
      markerDataset('Debt Spike (Δ > +5)', points, SPIKE, theme.critical, 0, 'triangle', theme),
      markerDataset('Refactoring Drop (Δ < −5)', points, DROP, theme.good, 180, 'triangle', theme),
      markerDataset('Stable', points, STABLE, theme.muted, 0, 'circle', theme),
    ],
  };
  return (
    <ChartFrame label={title} chartRef={chartRef}>
      <Line ref={chartRef} options={options} data={data} />
    </ChartFrame>
  );
}

/** FR-45: both repositories' Debt Score timelines aligned by commit sequence. */
export function ComparisonChart({ sides }) {
  const theme = useTheme();
  const chartRef = useRef(null);
  const title = 'Debt Score timelines, aligned by commit sequence';
  const length = Math.max(...sides.map((side) => side.timeline.length));
  const labels = Array.from({ length }, (_, index) => index + 1);
  const options = baseOptions(theme, { title, yTitle: 'Debt Score (0–100)', yMin: 0, yMax: 100 });
  options.plugins.tooltip.callbacks = {
    title: (items) => `Commit sequence ${items[0].label}`,
    label: (item) => {
      const point = sides[item.datasetIndex].timeline[item.dataIndex];
      return `${item.dataset.label}: ${formatNumber(item.raw, 2)} · ${shortSha(point.sha)} · `
        + `${point.classification}`;
    },
    afterLabel: (item) => {
      const point = sides[item.datasetIndex].timeline[item.dataIndex];
      return point.author ? `  ${point.author}, ${utcDate(point.timestamp)}` : '';
    },
  };
  const data = {
    labels,
    datasets: sides.map((side, slot) => lineDataset(theme,
      `${side.overview.owner}/${side.overview.name}`,
      labels.map((_, index) => side.timeline[index]?.debt_score ?? null), slot)),
  };
  return (
    <ChartFrame label={title} chartRef={chartRef}>
      <Line ref={chartRef} options={options} data={data} />
    </ChartFrame>
  );
}
