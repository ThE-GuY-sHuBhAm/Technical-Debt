// Analysis Progress view (SRS 4.1; FR-18, FR-38): a labelled progress bar per repository
// with the percentage and the commit being processed, an elapsed-time counter and a
// Cancel button. The server updates progress after every commit; this view asks for it
// once a second.
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { ErrorPanel } from '../common.jsx';
import { duration, shortSha } from '../format.js';
import { href, Link, navigate } from '../router.jsx';

const POLL_MS = 1000;
const DONE_PAUSE_MS = 800;

// FR-30: Pending (waiting for its turn) and Running analyses can both still be cancelled.
const active = (row) => row.status === 'Running' || row.status === 'Pending';

function stageText(row) {
  const progress = row.progress;
  if (row.status === 'Complete') return 'Analysis complete';
  if (row.status === 'Error') return 'Analysis stopped';
  if (!progress) return 'Starting';
  if (progress.stage === 'queued') return 'Waiting for the other analysis to finish';
  if (progress.stage === 'cloning') return 'Cloning the repository and reading its history';
  return `Commit ${progress.processed} of ${progress.total} analysed: ${shortSha(progress.current_sha)}`;
}

function percentOf(row) {
  if (row.status === 'Complete') return 100;
  return row.progress?.percent ?? 0;
}

export default function Progress({ params }) {
  const ids = params.getAll('repo');
  const next = params.get('next');
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [now, setNow] = useState(Date.now());
  const [cancelling, setCancelling] = useState(false);
  const key = ids.join(',');

  useEffect(() => {
    let live = true;
    let timer;
    const poll = async () => {
      try {
        const answer = await Promise.all(ids.map((id) => api.overview(id)));
        if (!live) return;
        setRows(answer);
        setError(null);
        if (answer.every((row) => row.status === 'Complete')) {
          // TC-099: the bar shows 100 % for a moment before the results open.
          timer = setTimeout(() => navigate(next === 'compare'
            ? href('/compare', { repo_1: ids[0], repo_2: ids[1] })
            : href('/overview', { repo: ids[0] }), true), DONE_PAUSE_MS);
          return;
        }
        if (answer.some(active)) {
          timer = setTimeout(poll, POLL_MS);
        }
      } catch (failure) {
        if (live) { setError(failure); timer = setTimeout(poll, POLL_MS * 3); }
      }
    };
    poll();
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { live = false; clearTimeout(timer); clearInterval(clock); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, next]);

  const cancel = async () => {
    setCancelling(true);
    await Promise.all((rows ?? []).filter(active)
      .map((row) => api.cancel(row.repository_id).catch(() => null)));
    navigate('/');
  };

  if (ids.length === 0) {
    return <ErrorPanel error={{ type: 'No analysis chosen', cause: 'This page needs a repository.',
      action: 'Start an analysis on the Home page.' }} />;
  }
  const running = (rows ?? []).some(active);
  const failed = (rows ?? []).filter((row) => row.status === 'Error');

  return (
    <section className="panel stack">
      <h1>Analysis in progress</h1>
      <ErrorPanel error={error} />
      {(rows ?? []).map((row) => {
        const percent = percentOf(row);
        const elapsed = (now - Date.parse(row.requested_at)) / 1000;
        return (
          <div key={row.repository_id} className="progress-block">
            <div className="progress-head">
              <strong>{row.owner}/{row.name}</strong>
              <span className="muted">Elapsed {duration(elapsed)}</span>
            </div>
            <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={percent} aria-label={`Progress of ${row.owner}/${row.name}`}>
              <div className="bar-fill" style={{ width: `${percent}%` }} />
            </div>
            <div className="progress-head">
              <span>{stageText(row)}</span>
              <strong>{percent}%</strong>
            </div>
            {row.status === 'Error' && row.error && <ErrorPanel error={row.error} />}
          </div>
        );
      })}
      <div className="actions">
        {running && (
          <button type="button" className="secondary" onClick={cancel} disabled={cancelling}>
            {cancelling ? 'Cancelling…' : 'Cancel'}
          </button>
        )}
        {failed.length > 0 && !running && <Link to="/" className="button">Back to Home</Link>}
      </div>
      {running && (
        <p className="muted">
          Cancel stops the analysis after the commit being processed and returns to Home;
          nothing new is stored.
        </p>
      )}
    </section>
  );
}
