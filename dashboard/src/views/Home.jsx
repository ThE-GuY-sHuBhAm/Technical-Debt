// Home / Input view (SRS 4.1; FR-01, FR-02, FR-04, FR-08, FR-22): the URL form and the
// repositories already analysed. The URL field has focus on load, Submit stays disabled
// until a URL is entered, and errors appear inline without a page reload.
import { useState } from 'react';
import { api } from '../api.js';
import { ErrorPanel, Loaded, useLoad } from '../common.jsx';
import { utcDate } from '../format.js';
import { href, Link, navigate } from '../router.jsx';

export default function Home() {
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [sampling, setSampling] = useState('2');
  const [refresh, setRefresh] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const history = useLoad(() => api.repositories(), []);

  const submit = async (event) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body = { url: url.trim(), sampling_n: Number(sampling), refresh };
      if (token.trim()) body.token = token.trim();
      const answer = await api.submit(body);
      setToken(''); // the token is used for this request only (FR-04)
      navigate(answer.cached ? href('/overview', { repo: answer.repository_id })
        : href('/progress', { repo: answer.repository_id }));
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <section className="panel">
        <h1>Analyse a repository</h1>
        <p className="muted">
          Enter a public GitHub repository. TDEA clones it, measures every Python file at
          each analysed commit and stores the results on this computer.
        </p>
        <form className="form" onSubmit={submit} noValidate>
          <label htmlFor="url">GitHub Repository URL</label>
          <input id="url" name="url" type="url" autoFocus autoComplete="off" spellCheck="false"
            placeholder="https://github.com/owner/repository" value={url}
            onChange={(event) => setUrl(event.target.value)} aria-describedby="url-help" />
          <span id="url-help" className="hint">For example https://github.com/psf/requests</span>

          <label htmlFor="token">Personal Access Token (optional)</label>
          <input id="token" name="token" type="password" autoComplete="off" value={token}
            onChange={(event) => setToken(event.target.value)} aria-describedby="token-help" />
          <span id="token-help" className="hint">
            Raises GitHub&apos;s rate limit. It is used for this request only and never stored.
          </span>

          <label htmlFor="sampling">Sampling interval N</label>
          <input id="sampling" name="sampling" type="number" min="1" step="1" value={sampling}
            onChange={(event) => setSampling(event.target.value)} aria-describedby="sampling-help" />
          <span id="sampling-help" className="hint">
            Above 100 commits, every N-th commit is analysed, plus the latest one.
          </span>

          <label className="check">
            <input type="checkbox" checked={refresh}
              onChange={(event) => setRefresh(event.target.checked)} />
            Analyse again even if the repository is already in the cache
          </label>

          <div className="actions">
            <button type="submit" disabled={!url.trim() || busy}>
              {busy ? 'Checking…' : 'Analyse'}
            </button>
          </div>
        </form>
        <ErrorPanel error={error} />
      </section>

      <section className="panel">
        <h2>Analysed repositories</h2>
        <Loaded state={history} what="the analysis history">
          {(rows) => (rows.length === 0 ? <p className="muted">No repository has been analysed yet.</p> : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Repository</th><th scope="col">Status</th>
                    <th scope="col" className="num">Commits analysed</th>
                    <th scope="col">Last completed</th><th scope="col"><span className="sr-only">Open</span></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.repository_id}>
                      <td>{row.owner}/{row.name}</td>
                      <td><span className={`status status-${row.status.toLowerCase()}`}>{row.status}</span></td>
                      <td className="num">{row.commits_analysed ?? '–'}</td>
                      <td>{utcDate(row.last_completed_at)}</td>
                      <td>
                        {row.status === 'Running' || row.status === 'Pending'
                          ? <Link to={href('/progress', { repo: row.repository_id })}>Show progress</Link>
                          : <Link to={href('/overview', { repo: row.repository_id })}>Open</Link>}
                      </td>
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
