// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';
import sitemap from '@astrojs/sitemap';
import mdx from '@astrojs/mdx';
import { loadClinics, isPermanentlyClosed } from './src/data/clinics.js';

// Build a set of URL paths for permanently-closed clinics so we can exclude
// them from the sitemap. Their pages still build (with noindex meta) so people
// searching the specific clinic name still find an informational landing page,
// but we don't want to actively advertise them to crawlers.
const closedPaths = new Set(
  loadClinics()
    .filter(isPermanentlyClosed)
    .map((c) => `https://trtverified.com/clinics/${c.stateSlug}/${c.citySlug}/${c.slug}/`)
);

export default defineConfig({
  site: 'https://trtverified.com',
  trailingSlash: 'always',

  vite: {
    plugins: [tailwindcss()],
  },

  integrations: [mdx(), sitemap({ filter: (page) => !closedPaths.has(page) })],
});
