import './styles/index.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { routes } from '@/app/routes';
import { DataProvider } from '@/features/data/data-provider';
import { registerServiceWorker } from '@/pwa/register-sw';
import { ThemeProvider } from '@/theme/theme-provider';

const container = document.getElementById('root');
if (!container) throw new Error('index.html is missing <div id="root">');

const router = createBrowserRouter(routes);

createRoot(container).render(
  <StrictMode>
    <ThemeProvider>
      <DataProvider layout="fullscreen">
        <RouterProvider router={router} />
      </DataProvider>
    </ThemeProvider>
  </StrictMode>,
);

registerServiceWorker({ enabled: import.meta.env.PROD });
