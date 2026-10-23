// Small pieces shared by the views: data loading and the three-part error panel.
import { useEffect, useState } from 'react';

/** Load data with ``load`` whenever ``deps`` change; return {data, error, loading}. */
export function useLoad(load, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  useEffect(() => {
    let live = true;
    setState({ data: null, error: null, loading: true });
    load().then(
      (data) => live && setState({ data, error: null, loading: false }),
      (error) => live && setState({ data: null, error, loading: false }),
    );
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

/** An FR-05 / NFR-11 error: what happened, why, and what to do next. */
export function ErrorPanel({ error, children }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      <strong>{error.type ?? 'Something went wrong'}</strong>
      <p>{error.cause ?? 'The request could not be completed.'}</p>
      <p className="action">{error.action ?? 'Try again.'}</p>
      {children}
    </div>
  );
}

/** A placeholder while data loads. */
export function Loading({ what = 'results' }) {
  return <p className="muted" aria-live="polite">Loading {what}…</p>;
}

/** Render ``children(data)`` once loaded, or the loading line, or the error panel. */
export function Loaded({ state, what, children }) {
  if (state.loading) return <Loading what={what} />;
  if (state.error) return <ErrorPanel error={state.error} />;
  return children(state.data);
}
