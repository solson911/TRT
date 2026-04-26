export async function onRequest({ request, next }) {
  const url = new URL(request.url);

  if (url.hostname === 'www.trtindex.com') {
    url.hostname = 'trtindex.com';
    return Response.redirect(url.toString(), 301);
  }

  return next();
}
