// HTTP client for the TDEA REST API (tdea/api/routes.py). The dashboard reaches the
// backend over HTTP only (NFR-14). Every failure is turned into an ApiFailure with the
// three FR-05 parts, so views never show a raw exception (NFR-11).

export class ApiFailure extends Error {
  constructor(status, error) {
    super(error.cause);
    this.status = status;
    this.type = error.type;
    this.cause = error.cause;
    this.action = error.action;
  }
}

const UNREACHABLE = {
  type: 'Server unreachable',
  cause: 'The TDEA server did not answer the request.',
  action: 'Check that "tdea serve" is still running, then try again.',
};

async function request(method, path, body) {
  const options = { method, headers: { Accept: 'application/json' } };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiFailure(0, UNREACHABLE);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiFailure(response.status, data?.error ?? {
      type: 'Request failed',
      cause: `The server answered with status ${response.status}.`,
      action: 'Try again; if it keeps failing, restart TDEA.',
    });
  }
  return data;
}

const repo = (id) => `/api/repositories/${encodeURIComponent(id)}`;

export const api = {
  submit: (body) => request('POST', '/api/analyses', body),
  submitPair: (body) => request('POST', '/api/comparisons', body),
  repositories: () => request('GET', '/api/repositories'),
  overview: (id) => request('GET', repo(id)),
  cancel: (id) => request('POST', `${repo(id)}/cancel`),
  series: (id) => request('GET', `${repo(id)}/commits`),
  commit: (id, sha) => request('GET', `${repo(id)}/commits/${encodeURIComponent(sha)}`),
  highRisk: (id) => request('GET', `${repo(id)}/high-risk`),
  patterns: (id) => request('GET', `${repo(id)}/patterns`),
  comparison: (first, second) => request(
    'GET', `/api/comparison?repo_1=${encodeURIComponent(first)}&repo_2=${encodeURIComponent(second)}`),
};

export const downloads = {
  csv: (id) => `${repo(id)}/export.csv`,
  pdf: (id) => `${repo(id)}/report.pdf`,
  comparisonCsv: (first, second) =>
    `/api/comparison.csv?repo_1=${encodeURIComponent(first)}&repo_2=${encodeURIComponent(second)}`,
};
