# Search

The **Search** page provides full-text search across the entire database: cases, subjects, clients, findings, documents, and comments.

## Basic Search

1. Type a search term in the search field
2. Results are grouped by category (Cases, Subjects, Clients, Findings)
3. Click a result to navigate directly

The search query searches the following fields:

- **Cases**: title, description, tags
- **Subjects**: name, notes
- **Clients**: name, contact person
- **Findings**: data, type, source

## Full-Text Search (FTS)

When PostgreSQL is used, **full-text search** is available for more relevant results:

- Uses PostgreSQL `tsvector` / `tsquery` with ranking
- Supports prefix matching
- Results are sorted by relevance

On SQLite, falls back to `LIKE`-based search (slower, less accurate).

## OSINT Search

The **OSINT Search** feature (found in the Search tab or via the SpiderFoot menu) offers:

### Email Search
Searches for an email address in:
- Have I Been Pwned (data breaches)
- Social media
- Open sources

### Username Search
Searches a username across hundreds of social media platforms via:
- Brave Search API (if configured)
- Open sources

### Phone Search
Enriches a phone number with:
- Carrier information
- Line type
- WhatsApp/Telegram presence

## Exporting Search Results

Results can be exported to CSV. When there are more than 5,000 results, you get a warning. The export uses `yield_per(200)` to prevent memory issues.

## Keyboard Navigation

On the search results page:

- **s** — focus the search field
- **j** — selection down
- **k** — selection up
- **Enter** — open selected item

## Tips

- Search is **case-insensitive**
- Use specific terms for better results
- For FTS (PostgreSQL), common words (stopwords) are ignored
- You can search by date by entering the date in the search field (e.g. "2024-01")
