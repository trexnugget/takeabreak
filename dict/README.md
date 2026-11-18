# TakeABreak reader dictionary

`wordnet-1.db.gz` is the offline dictionary the app downloads the first time someone long-presses a word
in the reader (4.32). It's a gzipped SQLite file built from WordNet 3.1 (with WordNet 3.0's exception
lists for irregular forms) by `devtools/dict/build_wordnet.py` in the app's repo.

- `syn(id, pos, words, gloss)`: one row per synset; `words` is `|`-separated, `gloss` is WordNet's
  definition followed by its quoted examples.
- `word(lemma, senses)`: comma-separated synset ids, most used part of speech first.
- `exc(form, lemma, pos)`: irregular forms (went → go, mice → mouse).
- `meta(k, v)`: version, source, word count.

A new pack gets a new number (`wordnet-2.db.gz`); old app versions keep downloading the one they know.

WordNet 3.1 Copyright 2011 by Princeton University. All rights reserved. Used under the WordNet license
(`WORDNET-LICENSE.txt`). THIS SOFTWARE AND DATABASE IS PROVIDED "AS IS" AND PRINCETON UNIVERSITY MAKES NO
REPRESENTATIONS OR WARRANTIES, EXPRESS OR IMPLIED.
