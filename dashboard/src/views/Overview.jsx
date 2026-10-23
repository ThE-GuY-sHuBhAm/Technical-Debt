// Repository Overview (FR-30, FR-35, FR-29): the summary card, the Debt Score timeline
// and the debt patterns found in it.
import { api } from '../api.js';
import { DebtScoreChart } from '../charts.jsx';
import { ErrorPanel, Loaded, useLoad } from '../common.jsx';
import { shortSha, utcDate } from '../format.js';
import { href, Link, navigate } from '../router.jsx';

export function OverviewCard({ overview }) {
  return (
    <section className="panel card" aria-label="Repository overview">
      <div>
        <p className="eyebrow">Repository</p>
        <h1>{overview.name}</h1>
        <p className="muted">Owner {overview.owner} · <a href={overview.url} target="_blank"
          rel="noreferrer">{overview.url}</a></p>
      </div>
      <dl className="facts">
        <div><dt>Status</dt><dd><span className={`status status-${overview.status.toLowerCase()}`}>
          {overview.status}</span></dd></div>
        <div><dt>Commits analysed</dt><dd>{overview.commits_analysed ?? '–'}
          {overview.commits_total ? <span className="muted"> of {overview.commits_total}</span> : null}</dd></div>
        <div><dt>Sampling interval</dt><dd>N = {overview.sampling_n}</dd></div>
        <div><dt>Files skipped</dt><dd>{overview.file_errors}</dd></div>
        <div><dt>Last completed analysis</dt><dd>{utcDate(overview.last_completed_at)}</dd></div>
      </dl>
    </section>
  );
}

export default function Overview({ repo }) {
  const overview = useLoad(() => api.overview(repo), [repo]);
  const series = useLoad(() => api.series(repo), [repo]);
  const patterns = useLoad(() => api.patterns(repo), [repo]);
  const select = (sha) => navigate(href('/commits', { repo, sha }));

  return (
    <div className="stack">
      <Loaded state={overview} what="the repository">
        {(data) => (
          <>
            <OverviewCard overview={data} />
            {data.status === 'Running' && (
              <p><Link to={href('/progress', { repo })}>An analysis is running: show its progress</Link></p>
            )}
            {data.error && <ErrorPanel error={data.error} />}
          </>
        )}
      </Loaded>
      <section className="panel">
        <Loaded state={series} what="the Debt Score timeline">
          {(points) => <DebtScoreChart points={points} onSelect={select} />}
        </Loaded>
      </section>
      <section className="panel">
        <h2>Debt patterns</h2>
        <Loaded state={patterns} what="the debt patterns">
          {(rows) => (rows.length === 0
            ? <p className="muted">No Steady Growth, Refactoring Event or Stable Phase pattern was found.</p>
            : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th scope="col">Pattern</th><th scope="col">Commits</th>
                    <th scope="col" className="num">Length</th><th scope="col">From</th><th scope="col">To</th></tr></thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={`${row.label}-${row.first_sequence}`}>
                        <td>{row.label}</td>
                        <td>{row.first_sequence}–{row.last_sequence}</td>
                        <td className="num">{row.length}</td>
                        <td><code>{shortSha(row.first_sha)}</code></td>
                        <td><code>{shortSha(row.last_sha)}</code></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
        </Loaded>
      </section>
    </div>
  );
}
