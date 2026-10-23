// Export (FR-40 to FR-43): the CSV dataset and the PDF report, both built from the
// stored results without re-running any pipeline stage.
import { api, downloads } from '../api.js';
import { Loaded, useLoad } from '../common.jsx';

export default function Export({ repo }) {
  const overview = useLoad(() => api.overview(repo), [repo]);
  return (
    <section className="panel stack">
      <h1>Export</h1>
      <Loaded state={overview} what="the repository">
        {(data) => (data.last_completed_at ? (
          <div className="export-grid">
            <div className="export">
              <h2>Metrics CSV</h2>
              <p className="muted">One row per analysed commit: commit_hash, commit_sequence,
                timestamp, author, SLOC, mean_CC, mean_MI, total_smells, smell_density,
                debt_score, debt_delta and debt_classification.</p>
              <a className="button" href={downloads.csv(repo)} download>Download CSV</a>
            </div>
            <div className="export">
              <h2>PDF report</h2>
              <p className="muted">Repository metadata, the Debt Score timeline, the top-10
                high-risk files, statistics for each metric, debt patterns and a methodology
                note.</p>
              <a className="button" href={downloads.pdf(repo)} download>Download PDF</a>
            </div>
          </div>
        ) : <p className="muted">This repository has no completed analysis to export yet.</p>)}
      </Loaded>
    </section>
  );
}
