# NPTEL CSE Course & Syllabus Scraper

Scrapes Computer Science and Engineering (discipline prefix `106`) courses from the NPTEL portal:

- **PDF syllabi**: Downloaded directly from `https://archive.nptel.ac.in/content/syllabus_pdf/<COURSE_ID>.pdf` (or any syllabus PDF linked on the course page) and saved as `<YEAR> - <COURSE_NAME>.pdf`.
- **Webpage syllabi**: When no PDF is available, extracts the course info and syllabus from `https://nptel.ac.in/courses/<COURSE_ID>` and saves it as `<YEAR> - <COURSE_NAME>.md`.
- **Metadata index**: Exports `courses_metadata.json` and `courses_metadata.csv` in the output folder.

## Setup & Run (using `uv`)

```bash
uv sync
uv run playwright install chromium
```

Test on the first 5 courses:

```bash
uv run python scrape_nptel_cse.py --limit 5
```

Run for all discovered CSE courses:

```bash
uv run python scrape_nptel_cse.py
```

## Options

- `-o`, `--output`: Output directory (default: `nptel_cse_syllabi`)
- `--limit`: Max number of courses to process (for quick testing)
- `--delay`: Seconds to wait between requests (default: `0.5`)
- `--headed`: Show the Chromium browser window while scraping
