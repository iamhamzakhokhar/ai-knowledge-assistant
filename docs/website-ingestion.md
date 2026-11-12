# Import a website

In the dashboard, select a team and open **Websites**. Owners and admins can
submit a public HTTP or HTTPS URL. Select **Use sitemap** to submit an XML sitemap
or sitemap index. Members can see progress and open the results. Each page shows
its requested URL, final URL, title, and outcome when available. Completed pages
include a plain-text preview.

The crawler follows links and redirects within the submitted origin, including
the scheme, hostname and port. Submit the destination's canonical URL when it
redirects from HTTP to HTTPS or to a different hostname. Default HTTP and HTTPS
ports are supported. Query strings and fragments are removed before fetching and deduplication. The same
normalized URL and sitemap setting reuse the existing record within a team.

The crawler checks every resolved address and every redirect before connecting.
It connects directly to a validated numeric address and checks HTTPS certificates
against the original hostname. HTTPS requires TLS 1.2 or later. Private, loopback, link-local, metadata and
ambiguous address forms are blocked. It sends no browser cookies or account
credentials. The document worker stays on its internal network. The website
worker has a dedicated egress network for crawl requests.

`KnowledgeDeskCrawler` checks `/robots.txt` before fetching content and follows
the matching rules. A missing robots file permits fetching. An unavailable or
unreadable policy stops the crawl. See [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html)
and the [sitemap protocol](https://www.sitemaps.org/protocol.html).

| Limit                         | Value                                       |
| ----------------------------- | ------------------------------------------- |
| Page results                  | 20, plus up to four sitemap diagnostics     |
| Sitemap files                 | 4                                           |
| HTTP requests                 | 50, including redirects and policy requests |
| Link and sitemap index depth  | 2                                           |
| Redirects per request         | 3                                           |
| Response body                 | 512 KiB                                     |
| Combined response body budget | 4 MiB                                       |
| Fetch time                    | 5 seconds per hop and 30 seconds per crawl  |
| Extracted text per page       | 48 KiB                                      |

Pages can be HTML, XHTML or plain text. Sitemaps use XML. The crawler reads text
and links from the response. Scripts and page assets are outside the crawl.

## Recrawl and recovery

**Recrawl** starts a new attempt after a crawl completes, fails or expires.
Each import supports three attempts. The dashboard keeps the last published
snapshot visible until a later attempt publishes a replacement. A successful
attempt publishes its page results together, including skipped and failed
pages. A failed attempt shows its diagnostics and keeps any earlier snapshot.

The API commits the queued record before sending a JSON job containing only the
team, import and attempt IDs. The worker checks the initiating user's current
role before crawling and before publishing. Attempts use a five-minute queue
lease, a 75-second processing lease and a 45-second worker timeout. A late job
cannot publish over a newer attempt.

CI uses an internal-only website worker with a controlled hosts entry to test
destination rejection through the real queue. The successful crawl test uses a
fixture transport and real PostgreSQL to verify sitemap traversal, redirects,
deduplication, page failures, progress and recrawl publication.
