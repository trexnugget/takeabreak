# TakeABreak library catalog

Built every week by `.github/workflows/books.yml` from Project Gutenberg's public-domain catalog data
(`catalog/build_catalog.py`). English books only, and only where every author and translator died at least
61 years ago (free to read in India) and Gutenberg marks the book public domain.

- `v1/index.tsv`: id, title, author, words, tags (most read first)
- `v1/shelves.tsv`: key, title, subtitle, comma-separated ids
- `v1/about/<id div 100>.tsv`: id, short description
- `v1/covers.tsv`: cover check cache (r = real cover, p = Gutenberg placeholder)
- `v1/meta.txt`: build date and counts

The books themselves are downloaded by the app from a Gutenberg mirror; nothing here is a book.
