// High-Risk Files (FR-28, FR-36): the ten highest-risk files, sortable ascending and
// descending by any column. The ranking rule is the OI-03 proposal the backend follows.
import { useState } from 'react';
import { api } from '../api.js';
import { Loaded, useLoad } from '../common.jsx';
import { formatNumber } from '../format.js';

const CLASS_ORDER = { High: 3, Medium: 2, Low: 1 };
const COLUMNS = [
  { key: 'rank', label: 'Rank', numeric: true },
  { key: 'path', label: 'File path' },
  { key: 'mean_cc', label: 'Mean CC', numeric: true, digits: 2 },
  { key: 'mean_smell_density', label: 'Mean smell density', numeric: true, digits: 3 },
  { key: 'mean_mi', label: 'Mean MI', numeric: true, digits: 1 },
  { key: 'risk_class', label: 'Risk classification' },
  { key: 'dominant_metric', label: 'Dominant metric' },
];

function sortValue(row, key) {
  if (key === 'risk_class') return CLASS_ORDER[row.risk_class] ?? 0;
  return row[key];
}

export function SortableTable({ rows }) {
  // Rows start in rank order with no column selected, so that the first click on any
  // heading, Rank included, sorts ascending and the next one descending (TC-095).
  const [sort, setSort] = useState({ key: null, ascending: true });
  const ranked = rows.map((row, index) => ({ ...row, rank: index + 1 }));
  const sorted = sort.key === null ? ranked : [...ranked].sort((a, b) => {
    const left = sortValue(a, sort.key);
    const right = sortValue(b, sort.key);
    const order = typeof left === 'string' ? left.localeCompare(right) : left - right;
    return (sort.ascending ? order : -order) || a.rank - b.rank;
  });
  const toggle = (key) => setSort((old) => ({ key,
    ascending: old.key === key ? !old.ascending : true }));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {COLUMNS.map((column) => {
              const active = sort.key === column.key;
              return (
                <th key={column.key} scope="col" className={column.numeric ? 'num' : undefined}
                  aria-sort={active ? (sort.ascending ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" className="sort" onClick={() => toggle(column.key)}>
                    {column.label}
                    <span aria-hidden="true" className="sort-mark">
                      {active ? (sort.ascending ? '▲' : '▼') : '↕'}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.path}>
              {COLUMNS.map((column) => {
                if (column.key === 'risk_class') {
                  return <td key={column.key}><span className={`risk risk-${row.risk_class.toLowerCase()}`}>
                    {row.risk_class}</span></td>;
                }
                const value = row[column.key];
                return (
                  <td key={column.key} className={column.numeric ? 'num' : undefined}>
                    {column.digits !== undefined ? formatNumber(value, column.digits)
                      : column.key === 'path' ? <code>{value}</code> : value}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function HighRisk({ repo }) {
  const rows = useLoad(() => api.highRisk(repo), [repo]);
  return (
    <section className="panel stack">
      <h1>High-risk files</h1>
      <p className="muted">
        Files are ranked by the mean of their normalised mean cyclomatic complexity and
        normalised mean smell density over the analysed commits; risk classes are terciles
        of that score. Select a column heading to sort by it; select it again to reverse.
      </p>
      <Loaded state={rows} what="the high-risk files">
        {(data) => (data.length === 0 ? <p className="muted">No files were measured.</p>
          : <SortableTable rows={data} />)}
      </Loaded>
    </section>
  );
}
