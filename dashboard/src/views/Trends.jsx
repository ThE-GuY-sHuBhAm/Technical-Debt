// Metric Trends (FR-31 to FR-35): the five trend charts. Clicking a commit on any of
// them opens its per-file breakdown (FR-37).
import { api } from '../api.js';
import { ComplexityChart, DebtScoreChart, MaintainabilityChart, SlocChart,
  SmellChart } from '../charts.jsx';
import { Loaded, useLoad } from '../common.jsx';
import { href, navigate } from '../router.jsx';

export default function Trends({ repo }) {
  const series = useLoad(() => api.series(repo), [repo]);
  const select = (sha) => navigate(href('/commits', { repo, sha }));
  return (
    <div className="stack">
      <h1>Metric trends</h1>
      <Loaded state={series} what="the metric series">
        {(points) => (
          <div className="chart-grid">
            <section className="panel"><SlocChart points={points} onSelect={select} /></section>
            <section className="panel"><ComplexityChart points={points} onSelect={select} /></section>
            <section className="panel"><SmellChart points={points} onSelect={select} /></section>
            <section className="panel"><MaintainabilityChart points={points} onSelect={select} /></section>
            <section className="panel wide"><DebtScoreChart points={points} onSelect={select} /></section>
          </div>
        )}
      </Loaded>
    </div>
  );
}
