// Research Comparison (FR-44 to FR-47): choose two repositories from the analysis
// history or analyse two new ones, then see both Debt Score timelines overlaid by
// commit sequence, the statistical summary panel and the comparison CSV. A refused URL's
// error appears under its own field, and both fields keep their values (TC-115).
import { useState } from 'react';
import { api, downloads } from '../api.js';
import { ComparisonChart } from '../charts.jsx';
import { ErrorPanel, Loaded, useLoad } from '../common.jsx';
import { formatNumber } from '../format.js';
import { href, navigate } from '../router.jsx';

function Chooser({ first, second }) {
  const history = useLoad(() => api.repositories(), []);
  const [left, setLeft] = useState(first ?? '');
  const [right, setRight] = useState(second ?? '');
  const [urls, setUrls] = useState({ url_1: '', url_2: '', token: '' });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const analyse = async (event) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body = { url_1: urls.url_1.trim(), url_2: urls.url_2.trim() };
      if (urls.token.trim()) body.token = urls.token.trim();
      const answer = await api.submitPair(body);
      setUrls((old) => ({ ...old, token: '' }));
      navigate(`/progress?repo=${answer.repo_1}&repo=${answer.repo_2}&next=compare`);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel stack">
      <h1>Research comparison</h1>
      <Loaded state={history} what="the analysis history">
        {(rows) => {
          const complete = rows.filter((row) => row.status === 'Complete');
          const options = complete.map((row) => (
            <option key={row.repository_id} value={row.repository_id}>{row.owner}/{row.name}</option>
          ));
          return (
            <form className="form two" onSubmit={(event) => {
              event.preventDefault();
              navigate(href('/compare', { repo_1: left, repo_2: right }));
            }}>
              <label htmlFor="repo-1">Repository 1 (from the analysis history)</label>
              <select id="repo-1" value={left} onChange={(event) => setLeft(event.target.value)}>
                <option value="">Choose…</option>{options}
              </select>
              <label htmlFor="repo-2">Repository 2 (from the analysis history)</label>
              <select id="repo-2" value={right} onChange={(event) => setRight(event.target.value)}>
                <option value="">Choose…</option>{options}
              </select>
              <div className="actions">
                <button type="submit" disabled={!left || !right || left === right}>Compare</button>
                {left && left === right && <span className="hint">Choose two different repositories.</span>}
              </div>
            </form>
          );
        }}
      </Loaded>
      <details>
        <summary>Or analyse two new repositories</summary>
        <form className="form" onSubmit={analyse} noValidate>
          <label htmlFor="url-1">First GitHub repository URL</label>
          <input id="url-1" type="url" autoComplete="off" value={urls.url_1}
            placeholder="https://github.com/owner/repository" aria-invalid={error?.field === 'url_1'}
            onChange={(event) => setUrls({ ...urls, url_1: event.target.value })} />
          {error?.field === 'url_1' && <ErrorPanel error={error} />}
          <label htmlFor="url-2">Second GitHub repository URL</label>
          <input id="url-2" type="url" autoComplete="off" value={urls.url_2}
            placeholder="https://github.com/owner/repository" aria-invalid={error?.field === 'url_2'}
            onChange={(event) => setUrls({ ...urls, url_2: event.target.value })} />
          {error?.field === 'url_2' && <ErrorPanel error={error} />}
          <label htmlFor="pair-token">Personal Access Token (optional)</label>
          <input id="pair-token" type="password" autoComplete="off" value={urls.token}
            onChange={(event) => setUrls({ ...urls, token: event.target.value })} />
          <div className="actions">
            <button type="submit" disabled={!urls.url_1.trim() || !urls.url_2.trim() || busy}>
              {busy ? 'Checking…' : 'Analyse both'}
            </button>
          </div>
        </form>
        {!error?.field && <ErrorPanel error={error} />}
      </details>
    </section>
  );
}

const SUMMARY_ROWS = [
  ['Mean Debt Score', (s) => formatNumber(s.mean_score, 2)],
  ['Peak Debt Score', (s) => formatNumber(s.peak_score, 2)],
  ['Debt Spikes', (s) => s.spikes],
  ['Refactoring Drops', (s) => s.drops],
  ['Overall trend', (s) => `${s.trend} (slope ${formatNumber(s.slope, 2, true)} per commit)`],
];

function Result({ first, second }) {
  const comparison = useLoad(() => api.comparison(first, second), [first, second]);
  return (
    <Loaded state={comparison} what="the comparison">
      {({ repositories: sides }) => (
        <>
          <section className="panel"><ComparisonChart sides={sides} /></section>
          <section className="panel stack">
            <h2>Statistical summary</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th scope="col">Measure</th>
                    {sides.map((side, slot) => (
                      <th key={side.overview.repository_id} scope="col" className="num">
                        <span className={`swatch swatch-${slot + 1}`} aria-hidden="true" />
                        {side.overview.owner}/{side.overview.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr><th scope="row">Commits analysed</th>
                    {sides.map((side) => <td key={side.overview.repository_id} className="num">{side.timeline.length}</td>)}</tr>
                  {SUMMARY_ROWS.map(([label, value]) => (
                    <tr key={label}><th scope="row">{label}</th>
                      {sides.map((side) => <td key={side.overview.repository_id} className="num">{value(side.summary)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted">Trend is the sign of the least-squares slope of the Debt Score
              over commit sequence; slopes within ±0.1 per commit count as Stable.</p>
            <div className="actions">
              <a className="button" href={downloads.comparisonCsv(first, second)} download>
                Download comparison CSV</a>
            </div>
          </section>
        </>
      )}
    </Loaded>
  );
}

export default function Compare({ params }) {
  const first = params.get('repo_1');
  const second = params.get('repo_2');
  return (
    <div className="stack">
      <Chooser first={first} second={second} />
      {first && second && <Result first={first} second={second} />}
    </div>
  );
}
