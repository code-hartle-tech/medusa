import { createRoot } from 'react-dom/client';
import ProductApp from './product/ProductApp';
import { LabProvider } from './lib/lab';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <LabProvider>
    <ProductApp />
  </LabProvider>,
);

// Register the service worker only for a real build. In dev, Vite serves
// modules the worker would happily cache, and a stale cached module is a
// confusing way to spend an afternoon.
//
// Offline is the normal case for this app, not an edge case: joined to the
// sensor's own access point the phone has no internet at all, and an app that
// will not launch without a server is useless in exactly the situation it
// exists for.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((error) => {
      // A failed registration costs offline support, not the app. Say so once
      // rather than failing the launch.
      console.warn('[medusa] offline support unavailable:', error);
    });
  });
}
