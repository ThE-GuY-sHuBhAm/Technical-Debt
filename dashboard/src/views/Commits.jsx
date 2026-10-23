// Commit History and drill-down (FR-37): every analysed commit, and for the selected one
// the per-file path, LOC, SLOC, CC with rank, MI and Pylint counts per category.
import { api } from '../api.js';
import { Loaded, useLoad } from '../common.jsx';
import { formatNumber, shortSha, utcDate } from '../format.js';
import { href, navigate } from '../router.jsx';

function DrillDown({ repo, sha }) {
  const detail = useLoad(() => api.commit(repo, sha), [repo, sha]);
  return (
    <section className="panel stack" aria-label="Commit drill-down" id="drill-down">
      <Loaded state={detail} what="the commit's files">
        {(commit) => (
          <>
            <div className="drill-head">
              <div>
                <p className="eyebrow">Commit {commit.sequence}</p>
                <h2><code>{commit.sha}</code></h2>
                <p>{commit.message}</p>
                <p className="muted">{commit.author} · {utcDate(commit.timestamp)}</p>
              </div>
              <button type="button" className="ghost"
                onClick={() => navigate(href('/commits', { repo }))}>Close</button>
            </div>
            {commit.file_errors > 0 && (
              <p className="note">{commit.file_errors} file(s) at this commit could not be measured
                and were skipped (empty, unparsable or undecodable).</p>
            )}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">File path</th><th scope="col" className="num">LOC</th>
                    <th scope="col" className="num">SLOC</th><th scope="col" className="num">Mean CC</th>
                    <th scope="col" className="num">Max CC</th><th scope="col">CC rank</th>
                    <th scope="col" className="num">MI</th>
                    <th scope="col" className="num" title="Convention">C</th>
                    <th scope="col" className="num" title="Refactor">R</th>
                    <th scope="col" className="num" title="Warning">W</th>
                    <th scope="col" className="num" title="Error">E</th>
                  </tr>
                </thead>
                <tbody>
                  {commit.files.map((file) => (
                    <tr key={file.path}>
                      <td>
                        <details>
                          <summary><code>{file.path}</code></summary>
                          {file.functions.length === 0 ? <p className="muted">No functions.</p> : (
                            <ul className="functions">
                              {file.functions.map((item) => (
                                <li key={`${item.name}-${item.line}`}><code>{item.name}</code> (line {item.line}):
                                  {' '}CC {item.complexity}, rank {item.rank}</li>
                              ))}
                            </ul>
                          )}
                        </details>
                      </td>
                      <td className="num">{file.loc}</td><td className="num">{file.sloc}</td>
                      <td className="num">{formatNumber(file.mean_cc, 2)}</td>
                      <td className="num">{file.max_cc}</td><td>{file.cc_rank}</td>
                      <td className="num">{file.mi}</td>
                      <td className="num">{file.convention}</td><td className="num">{file.refactor}</td>
                      <td className="num">{file.warning}</td><td className="num">{file.error}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {commit.changed_files.length > 0 && (
              <details>
                <summary>{commit.changed_files.length} changed file(s) in this commit</summary>
                <ul className="functions">
                  {commit.changed_files.map((item) => (
                    <li key={item.path}><code>{item.path}</code> ({item.change_type})</li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </Loaded>
    </section>
  );
}

export default function Commits({ repo, sha }) {
  const series = useLoad(() => api.series(repo), [repo]);
  return (
    <div className="stack">
      <h1>Commit history</h1>
      {sha && <DrillDown repo={repo} sha={sha} />}
      <section className="panel">
        <Loaded state={series} what="the commit history">
          {(points) => (
            <div className="table-wrap">
              <table className="clickable">
                <thead>
                  <tr>
                    <th scope="col" className="num">#</th><th scope="col">Commit</th>
                    <th scope="col">Date</th><th scope="col">Author</th><th scope="col">Message</th>
                    <th scope="col" className="num">SLOC</th><th scope="col" className="num">Debt Score</th>
                    <th scope="col" className="num">Δ</th><th scope="col">Classification</th>
                  </tr>
                </thead>
                <tbody>
                  {[...points].reverse().map((point) => (
                    <tr key={point.sha} className={point.sha === sha ? 'selected' : undefined}
                      onClick={() => navigate(href('/commits', { repo, sha: point.sha }))}>
                      <td className="num">{point.sequence}</td>
                      <td>
                        <a href={href('/commits', { repo, sha: point.sha })}
                          onClick={(event) => event.preventDefault()}><code>{shortSha(point.sha)}</code></a>
                      </td>
                      <td>{utcDate(point.timestamp)}</td>
                      <td>{point.author}</td>
                      <td className="message">{point.message}</td>
                      <td className="num">{point.sloc}</td>
                      <td className="num">{formatNumber(point.debt_score, 2)}</td>
                      <td className="num">{formatNumber(point.debt_delta, 2, true)}</td>
                      <td>{point.classification}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Loaded>
      </section>
    </div>
  );
}
