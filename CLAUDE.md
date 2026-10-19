# Project notes

## Live property manager (source of truth)

The synced Arelis property-management app lives at:
**https://claude.ai/artifact/Mb6FvdQFbEDt8KMeisomSU**

- Always update THIS artifact (publish with `url` set to it) — never create a new one.
- Its data (properties, incomes, expenses, settings) is in the artifact database; invoice files are artifact assets. Code updates do not touch the data.
- Source file in this repo: `arelis_properties.html`. The published page is this file without the
  `<!DOCTYPE>/<html>/<head>/<body>` wrapper tags.
