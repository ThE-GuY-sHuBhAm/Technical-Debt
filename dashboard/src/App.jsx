// The dashboard shell: persistent navigation between Repository Overview, Metric
// Trends, High-Risk Files, Commit History and Export (FR-39), plus Home and the
// Research Comparison view. The selected repository travels in the ?repo= parameter,
// so every section has its own address.
import { api } from './api.js';
import { ErrorPanel, useLoad } from './common.jsx';
import { href, Link, useRoute } from './router.jsx';
import Commits from './views/Commits.jsx';
import Compare from './views/Compare.jsx';
import Export from './views/Export.jsx';
import HighRisk from './views/HighRisk.jsx';
import Home from './views/Home.jsx';
import Overview from './views/Overview.jsx';
import Progress from './views/Progress.jsx';
import Trends from './views/Trends.jsx';

const SECTIONS = [
  { path: '/overview', label: 'Repository Overview', view: Overview },
  { path: '/trends', label: 'Metric Trends', view: Trends },
  { path: '/high-risk', label: 'High-Risk Files', view: HighRisk },
  { path: '/commits', label: 'Commit History', view: Commits },
  { path: '/export', label: 'Export', view: Export },
];

function RepositoryName({ repo }) {
  const overview = useLoad(() => api.overview(repo), [repo]);
  if (!overview.data) return null;
  return <span className="current-repo" title={overview.data.url}>
    {overview.data.owner}/{overview.data.name}</span>;
}

function Navigation({ path, repo }) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link to="/" className="brand">TDEA</Link>
        <nav aria-label="Dashboard sections">
          <ul>
            <li><Link to="/" aria-current={path === '/' ? 'page' : undefined}>New analysis</Link></li>
            {SECTIONS.map((section) => (
              <li key={section.path}>
                {repo ? (
                  <Link to={href(section.path, { repo })}
                    aria-current={path === section.path ? 'page' : undefined}>{section.label}</Link>
                ) : (
                  <span className="disabled" aria-disabled="true"
                    title="Open an analysed repository first">{section.label}</span>
                )}
              </li>
            ))}
            <li><Link to="/compare" aria-current={path === '/compare' ? 'page' : undefined}>
              Research Comparison</Link></li>
          </ul>
        </nav>
        {repo && <RepositoryName repo={repo} />}
      </div>
    </header>
  );
}

function NeedsRepository() {
  return (
    <ErrorPanel error={{ type: 'No repository selected',
      cause: 'This section shows the results of one analysed repository.',
      action: 'Open a repository from the list on the Home page, or analyse a new one.' }}>
      <Link to="/" className="button">Go to Home</Link>
    </ErrorPanel>
  );
}

export default function App() {
  const { path, params } = useRoute();
  const repo = params.get('repo');
  const section = SECTIONS.find((item) => item.path === path);
  let body;
  if (path === '/' || path === '/index.html') body = <Home />;
  else if (path === '/progress') body = <Progress params={params} />;
  else if (path === '/compare') body = <Compare params={params} />;
  else if (section) {
    const View = section.view;
    body = repo ? <View key={repo} repo={repo} sha={params.get('sha')} /> : <NeedsRepository />;
  } else {
    body = (
      <ErrorPanel error={{ type: 'Page not found', cause: `TDEA has no page at ${path}.`,
        action: 'Use the navigation bar, or go back to the Home page.' }} />
    );
  }
  return (
    <>
      <Navigation path={path} repo={section ? repo : null} />
      <main className="content">{body}</main>
    </>
  );
}
