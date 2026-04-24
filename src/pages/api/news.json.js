export const prerender = true;

export async function GET() {
  const modules = import.meta.glob('../news/*.{md,mdx}', { eager: true });

  const articles = Object.entries(modules)
    .map(([path, mod]) => {
      const fm = mod.frontmatter || {};
      const slug = path
        .replace(/^.*\/news\//, '')
        .replace(/\.(md|mdx)$/, '');
      return {
        slug,
        title: fm.title || slug,
        description: fm.description || '',
        category: fm.category || 'News',
        publishDate: fm.date || null,
        createdAt: fm.date || null,
        image: fm.image || null,
        source: fm.source || null,
        sourceUrl: fm.sourceUrl || null,
        url: `/news/${slug}/`,
      };
    })
    .filter((a) => a.slug)
    .sort((a, b) => {
      const aT = a.publishDate ? new Date(a.publishDate).getTime() : 0;
      const bT = b.publishDate ? new Date(b.publishDate).getTime() : 0;
      return bT - aT;
    });

  return new Response(JSON.stringify(articles, null, 2), {
    status: 200,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'public, max-age=300, s-maxage=600',
    },
  });
}
