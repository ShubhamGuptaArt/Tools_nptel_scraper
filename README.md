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
- `--courses-file`: Path to JSON list of courses (default: `cse_courses.json`)
- `--discover`: Also discover additional CSE (`106*`) courses from the NPTEL portal
- `--discover-all`: Discover all courses across all disciplines from the NPTEL portal
- `--resume`: Skip courses already present in `<output>/courses_metadata.json`
- `--exclude-metadata`: Path to an existing `courses_metadata.json` whose courses should be skipped (repeatable)
- `--workers`: Number of concurrent browser workers (default: `1`)
- `--limit`: Max number of courses to process (for quick testing)
- `--delay`: Seconds to wait between requests (default: `0.5`)
- `--headed`: Show the Chromium browser window while scraping

## Scraping Only Newer Courses in the Future

To discover all courses on NPTEL and scrape **only new courses** into `wholeNPTEL/` (skipping everything already in `wholeNPTEL/courses_metadata.json`):

```bash
uv run python scrape_nptel_cse.py --courses-file whole_nptel_courses.json -o wholeNPTEL --discover-all --resume --workers 10 --delay 0.1
```

Or to save only the newly discovered courses into a **separate folder** (e.g., `new_courses/`) while still skipping everything already scraped in `wholeNPTEL/`:

```bash
uv run python scrape_nptel_cse.py --courses-file whole_nptel_courses.json -o new_courses --discover-all --exclude-metadata wholeNPTEL/courses_metadata.json --workers 10 --delay 0.1
```
