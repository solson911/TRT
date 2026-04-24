// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';
import sitemap from '@astrojs/sitemap';
import mdx from '@astrojs/mdx';
import { loadClinics, isPermanentlyClosed, isHoneytoken } from './src/data/clinics.js';

// Sitemap exclusions: permanently-closed clinics (their detail pages still
// build with noindex so name-search still lands somewhere informative) and
// honeytoken/sentinel listings (kept out of search engines so they don't
// pollute results, but still rendered in shards/listings to catch scrapers).
const excludedPaths = new Set(
  loadClinics()
    .filter((c) => isPermanentlyClosed(c) || isHoneytoken(c))
    .map((c) => `https://trtindex.com/clinics/${c.stateSlug}/${c.citySlug}/${c.slug}/`)
);

export default defineConfig({
  site: 'https://trtindex.com',
  trailingSlash: 'always',

  vite: {
    plugins: [tailwindcss()],
  },

  integrations: [mdx(), sitemap({ filter: (page) => !excludedPaths.has(page) })],
});
