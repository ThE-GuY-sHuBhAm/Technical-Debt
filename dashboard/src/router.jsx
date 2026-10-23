// A minimal client-side router over the History API. Flask serves index.html for every
// non-API path, so each section has its own address and survives a reload.
import { useEffect, useState } from 'react';

function current() {
  return { path: window.location.pathname, params: new URLSearchParams(window.location.search) };
}

/** Return the current path and query parameters, updated on navigation. */
export function useRoute() {
  const [route, setRoute] = useState(current);
  useEffect(() => {
    const update = () => setRoute(current());
    window.addEventListener('popstate', update);
    return () => window.removeEventListener('popstate', update);
  }, []);
  return route;
}

/** Go to ``to`` without a page reload; ``replace`` keeps the history length. */
export function navigate(to, replace = false) {
  if (replace) window.history.replaceState(null, '', to);
  else window.history.pushState(null, '', to);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

/** Build a path with query parameters, leaving out empty ones. */
export function href(path, params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, value);
  });
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

/** An anchor that navigates inside the application. */
export function Link({ to, children, ...rest }) {
  const onClick = (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey
        || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(to);
  };
  return <a href={to} onClick={onClick} {...rest}>{children}</a>;
}
