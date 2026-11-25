#!/usr/bin/env python3
"""
NPTEL Computer Science & Engineering (CSE) Course & Syllabus Scraper.

Scrapes NPTEL CSE courses (discipline prefix 106 / Computer Science and Engineering):
1. Discovers CSE course IDs and metadata from https://nptel.ac.in/courses using Playwright
   (handles dynamically rendered JavaScript content).
2. For each course, visits https://nptel.ac.in/courses/<COURSE_ID> to extract:
   - Course title, year, instructor(s), institute, duration, and overview
   - Direct syllabus PDF link (or checks https://archive.nptel.ac.in/content/syllabus_pdf/<COURSE_ID>.pdf)
3. Saves the syllabus in `nptel_cse_syllabi/`:
   - As `<YEAR> - <COURSE_NAME>.pdf` if a PDF is available
   - As `<YEAR> - <COURSE_NAME>.md` (with full course info + webpage syllabus) if no PDF is found
4. Exports `courses_metadata.json` and `courses_metadata.csv` for downstream analysis.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

COURSES_PORTAL_URL = "https://nptel.ac.in/courses"
COURSE_DETAIL_URL = "https://nptel.ac.in/courses/{course_id}"
ARCHIVE_PDF_URL = "https://archive.nptel.ac.in/content/syllabus_pdf/{course_id}.pdf"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}


@dataclass
class CourseRecord:
    course_id: str
    title: str
    year: str
    instructor: str
    institute: str
    duration: str
    course_url: str
    syllabus_format: str  # "pdf" | "md" | "failed"
    syllabus_source_url: str
    saved_file: str
    details_file: str
    lessons_file: str


def sanitize_filename(name: str, max_length: int = 140) -> str:
    """Sanitize a string so it is safe as a filename across OSes."""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "Untitled_Course"
    return cleaned[:max_length].rstrip(" .")


def extract_year(text: str) -> str:
    """Extract a 4-digit course year (2005-2035), ignoring footer copyright lines."""
    filtered_lines = [
        line
        for line in text.splitlines()
        if not any(kw in line.lower() for kw in ("copyright", "©", "all rights reserved"))
    ]
    cleaned_text = "\n".join(filtered_lines)

    # Prefer explicit month-year or timeline patterns like "Jan-Apr 2024", "Jul 2023"
    month_year = re.findall(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z\-/\s]*\b(20(?:0[5-9]|[1-3]\d))\b",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if month_year:
        return month_year[-1]

    matches = re.findall(r"\b(20(?:0[5-9]|[1-3]\d))\b", cleaned_text)
    if matches:
        return matches[-1]
    return "UnknownYear"


def unique_filepath(directory: Path, stem: str, ext: str, course_id: str) -> Path:
    """Return a non-colliding file path in `directory`."""
    candidate = directory / f"{stem}.{ext}"
    if not candidate.exists():
        return candidate
    return directory / f"{stem} ({course_id}).{ext}"


def download_pdf(url: str, dest: Path, timeout: int = 25) -> bool:
    """Download a PDF if the response is genuinely a PDF file."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return False

    if resp.status_code != 200:
        return False

    content_type = resp.headers.get("content-type", "").lower()
    is_pdf = "application/pdf" in content_type or resp.content[:5] == b"%PDF-"
    if not is_pdf or len(resp.content) < 512:
        return False

    dest.write_bytes(resp.content)
    return True


def discover_cse_courses(page: Page, max_scrolls: int = 60, limit: Optional[int] = None) -> list[dict[str, str]]:
    """Load NPTEL courses portal and collect CSE (prefix 106) course IDs and basic info."""
    print(f"Loading {COURSES_PORTAL_URL} ...")
    page.goto(COURSES_PORTAL_URL, wait_until="networkidle", timeout=60000)

    # Attempt to select Computer Science and Engineering discipline filter if present
    filter_selectors = [
        "text=/Computer Science and Engineering/i",
        "label:has-text('Computer Science')",
        "option:has-text('Computer Science')",
    ]
    for selector in filter_selectors:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=2000):
                loc.click()
                page.wait_for_timeout(1500)
                print(f"Applied CSE filter via selector: {selector}")
                break
        except Exception:
            continue

    # Scroll to load lazy-rendered course cards
    prev_height = 0
    for _ in range(max_scrolls):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(800)
        curr_height = page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        prev_height = curr_height

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    discovered: dict[str, dict[str, str]] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].rstrip("/")
        parts = href.split("/")
        cid = parts[-1] if parts else ""
        if cid.isdigit() and cid.startswith("106"):
            card_text = " ".join(a.get_text(" ", strip=True).split())
            if cid not in discovered:
                discovered[cid] = {
                    "course_id": cid,
                    "hint_text": card_text,
                    "course_url": urljoin(COURSES_PORTAL_URL, a["href"]),
                }

    courses = [discovered[cid] for cid in sorted(discovered)]
    if limit is not None:
        courses = courses[:limit]
    print(f"Discovered {len(courses)} CSE (106*) courses.")
    return courses


def expand_all_weeks(page: Page) -> None:
    """Click collapsed week/module accordions so all weekly lessons are visible in the DOM."""
    # Expand any elements with aria-expanded="false" (skipping top nav menus)
    try:
        collapsed = page.locator("main [aria-expanded='false'], aside [aria-expanded='false'], [class*='week' i] [aria-expanded='false'], [class*='module' i] [aria-expanded='false']")
        count = min(collapsed.count(), 60)
        for i in range(count):
            try:
                el = collapsed.nth(i)
                if el.is_visible(timeout=300):
                    el.click(timeout=500)
                    page.wait_for_timeout(150)
            except Exception:
                continue
    except Exception:
        pass

    # Fallback: click visible Week N / Module N / Unit N headers
    try:
        week_headers = page.locator("text=/^(Week|Module|Unit)\\s+\\d+/i")
        count = min(week_headers.count(), 60)
        for i in range(count):
            try:
                el = week_headers.nth(i)
                if el.is_visible(timeout=300):
                    el.click(timeout=500)
                    page.wait_for_timeout(120)
            except Exception:
                continue
    except Exception:
        pass


def extract_structured_sections(soup: BeautifulSoup) -> list[str]:
    """Extract readable Markdown lines from the main content area."""
    container = soup.find("main") or soup.find("article") or soup.body
    if not container:
        return []

    sections: list[str] = []
    for el in container.find_all(["h1", "h2", "h3", "h4", "p", "li", "tr"]):
        txt = " ".join(el.get_text(" ", strip=True).split())
        if not txt:
            continue
        if el.name in ("h1", "h2"):
            sections.append(f"## {txt}")
        elif el.name in ("h3", "h4"):
            sections.append(f"### {txt}")
        elif el.name == "li":
            sections.append(f"- {txt}")
        else:
            sections.append(txt)

    deduped: list[str] = []
    for line in sections:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return deduped


def extract_weekly_lessons(soup: BeautifulSoup) -> str:
    """Extract weekly course content and complete lesson list as Markdown."""
    lines: list[str] = []
    seen: set[str] = set()

    # Look for week/module/unit containers or list items across the page
    week_pattern = re.compile(r"^(Week|Module|Unit)\s+\d+\b", re.IGNORECASE)
    lesson_containers = soup.find_all(["aside", "nav", "section", "div", "ul", "ol"])

    # First pass: collect explicit Week/Module headings and their list items
    for el in soup.find_all(["h2", "h3", "h4", "button", "summary", "li", "a"]):
        txt = " ".join(el.get_text(" ", strip=True).split())
        if not txt or len(txt) > 220:
            continue
        # Skip global site navigation links
        if txt.lower() in (
            "home", "courses", "initiatives", "about", "contact", "login",
            "sign in", "nptel", "swayam", "more", "help",
        ):
            continue

        if week_pattern.match(txt):
            entry = f"## {txt}"
            if entry not in seen:
                seen.add(entry)
                lines.append("")
                lines.append(entry)
        elif el.name == "li" or (el.name == "a" and "/courses/" not in (el.get("href") or "")):
            # Keep lesson items (avoid duplicating if already captured)
            if el.find_parent(["header", "footer"]):
                continue
            entry = f"- {txt}"
            if entry not in seen:
                seen.add(entry)
                lines.append(entry)

    if lines:
        return "\n".join(lines).strip()

    # Fallback to main structured sections
    fallback = extract_structured_sections(soup)
    return "\n\n".join(fallback)


def parse_course_page(page: Page, course_id: str, hint_text: str) -> dict[str, object]:
    """Visit a course detail page and extract metadata, PDF links, details, and weekly lessons."""
    url = COURSE_DETAIL_URL.format(course_id=course_id)
    try:
        page.goto(url, wait_until="networkidle", timeout=90000)
    except PlaywrightTimeoutError:
        # Fallback if a slow asset prevents networkidle
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
    page.wait_for_timeout(1000)

    # 1. Capture Course Details / About the Course tab if present
    details_sections: list[str] = []
    for tab_text in ("Course Details", "About the Course", "Course Info", "Syllabus"):
        try:
            tab = page.locator(f"button:has-text('{tab_text}'), a:has-text('{tab_text}')").first
            if tab.is_visible(timeout=800):
                tab.click()
                page.wait_for_timeout(600)
                tab_soup = BeautifulSoup(page.content(), "html.parser")
                details_sections.extend(extract_structured_sections(tab_soup))
        except Exception:
            pass

    # 2. Switch to Course Outline / Content tab and expand all weeks/modules
    for tab_text in ("Course Outline", "Course Layout", "Modules", "Content", "Lectures"):
        try:
            tab = page.locator(f"button:has-text('{tab_text}'), a:has-text('{tab_text}')").first
            if tab.is_visible(timeout=800):
                tab.click()
                page.wait_for_timeout(600)
                break
        except Exception:
            pass

    expand_all_weeks(page)
    soup = BeautifulSoup(page.content(), "html.parser")

    # NPTEL course pages typically render "<Course Title>, <Institute>" in an <h3>
    # (while <h1>/<h2> can be nav items like "Initiatives" or lecture titles like "L1-Introduction").
    title = ""
    institute = ""
    institute_keywords = ("iit ", "iisc", "iiit", "nit ", "institute", "university", "cmi", "imsc", "tifr")

    for h3 in soup.find_all("h3"):
        h3_text = " ".join(h3.get_text(" ", strip=True).split())
        if "," in h3_text and any(kw in h3_text.lower() for kw in institute_keywords):
            parts = h3_text.rsplit(",", 1)
            title = parts[0].strip()
            institute = parts[1].strip()
            break

    if not title and soup.title:
        page_title = " ".join(soup.title.get_text(" ", strip=True).split())
        page_title = re.sub(r"\s*[-|]\s*NPTEL.*$", "", page_title, flags=re.IGNORECASE).strip()
        if page_title and page_title.lower() not in ("nptel", "courses"):
            title = page_title

    if not title:
        for tag in ("h1", "h2"):
            el = soup.find(tag)
            if el and el.get_text(strip=True):
                cand = " ".join(el.get_text(" ", strip=True).split())
                if cand.lower() not in ("initiatives", "nptel", "courses"):
                    title = cand
                    break

    if not title:
        title = hint_text or f"NPTEL_Course_{course_id}"

    full_text = soup.get_text("\n", strip=True)

    # Year
    year = extract_year(full_text)
    if year == "UnknownYear":
        year = extract_year(hint_text)

    # Best-effort metadata extraction
    instructor = ""
    duration = ""
    for line in full_text.splitlines():
        low = line.lower()
        if not instructor and ("prof." in low or "instructor" in low):
            instructor = line.strip()
        elif not institute and any(kw in low for kw in institute_keywords) and len(line) < 100:
            institute = line.strip()
        elif not duration and ("weeks" in low or "week" in low) and len(line) < 60:
            duration = line.strip()

    # Collect any explicit PDF links on the course page
    pdf_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True).lower()
        if ".pdf" in href.lower() or "syllabus" in text:
            full_href = urljoin(url, href)
            if ".pdf" in full_href.lower():
                pdf_links.append(full_href)

    # Always include the standard archive PDF pattern
    archive_pdf = ARCHIVE_PDF_URL.format(course_id=course_id)
    if archive_pdf not in pdf_links:
        pdf_links.insert(0, archive_pdf)

    # Extract structured details and weekly lessons
    main_sections = extract_structured_sections(soup)
    # Merge tab details + main sections while preserving order and removing duplicates
    combined_details: list[str] = []
    seen_details: set[str] = set()
    for line in details_sections + main_sections:
        if line not in seen_details:
            seen_details.add(line)
            combined_details.append(line)

    weekly_lessons = extract_weekly_lessons(soup)

    return {
        "course_id": course_id,
        "title": title,
        "year": year,
        "instructor": instructor,
        "institute": institute,
        "duration": duration,
        "course_url": url,
        "pdf_links": pdf_links,
        "details_body": "\n\n".join(combined_details) if combined_details else full_text,
        "lessons_body": weekly_lessons or full_text,
    }


def _course_header_lines(info: dict[str, object], subtitle: str) -> list[str]:
    return [
        f"# {info['title']} — {subtitle}",
        "",
        "## Course Information",
        f"- **Course ID:** `{info['course_id']}`",
        f"- **Year:** {info['year']}",
        f"- **Instructor:** {info['instructor'] or 'N/A'}",
        f"- **Institute:** {info['institute'] or 'N/A'}",
        f"- **Duration:** {info['duration'] or 'N/A'}",
        f"- **Course URL:** {info['course_url']}",
        "",
        "---",
        "",
    ]


def write_markdown_syllabus(dest: Path, info: dict[str, object]) -> None:
    """Write fallback syllabus Markdown when no PDF is available."""
    lines = _course_header_lines(info, "Syllabus") + [
        "- **Syllabus Source:** Webpage (no PDF available)",
        "",
        str(info["details_body"]),
        "",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")


def write_details_markdown(dest: Path, info: dict[str, object]) -> None:
    """Write the Course Details page content to a Markdown file."""
    lines = _course_header_lines(info, "Course Details") + [
        "## Course Details",
        "",
        str(info["details_body"]),
        "",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")


def write_lessons_markdown(dest: Path, info: dict[str, object]) -> None:
    """Write the weekly course content and complete lesson list to a Markdown file."""
    lines = _course_header_lines(info, "Weekly Content & Lessons") + [
        "## Weekly Course Content & Complete Lesson List",
        "",
        str(info["lessons_body"]),
        "",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")


def save_metadata(output_dir: Path, records: list[CourseRecord], merge_existing: bool = False) -> None:
    """Export collected course metadata as JSON and CSV."""
    json_path = output_dir / "courses_metadata.json"
    csv_path = output_dir / "courses_metadata.csv"

    final_records = records
    if merge_existing and json_path.exists():
        existing_raw = json.loads(json_path.read_text(encoding="utf-8"))
        by_id = {str(item["course_id"]): item for item in existing_raw}
        for r in records:
            by_id[r.course_id] = asdict(r)
        final_dicts = list(by_id.values())
    else:
        final_dicts = [asdict(r) for r in final_records]

    json_path.write_text(
        json.dumps(final_dicts, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fieldnames = list(CourseRecord.__dataclass_fields__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in final_dicts:
            writer.writerow(row)


def load_courses_list(
    courses_file: Optional[Path],
    page: Page,
    discover: bool,
    limit: Optional[int],
    course_ids: Optional[list[str]] = None,
) -> list[dict[str, str]]:
    """Load CSE courses from cse_courses.json if available, optionally merging live discovery."""
    by_id: dict[str, dict[str, str]] = {}

    if courses_file and courses_file.exists():
        raw = json.loads(courses_file.read_text(encoding="utf-8"))
        for entry in raw:
            cid = str(entry["course_id"])
            by_id[cid] = {
                "course_id": cid,
                "hint_text": str(entry.get("title", "")),
                "course_url": str(entry.get("course_url", COURSE_DETAIL_URL.format(course_id=cid))),
            }
        print(f"Loaded {len(by_id)} CSE courses from {courses_file.name}.")

    if discover or not by_id:
        for item in discover_cse_courses(page):
            cid = item["course_id"]
            if cid not in by_id:
                by_id[cid] = item

    if course_ids:
        courses = [
            by_id.get(
                cid,
                {
                    "course_id": cid,
                    "hint_text": "",
                    "course_url": COURSE_DETAIL_URL.format(course_id=cid),
                },
            )
            for cid in course_ids
        ]
    else:
        courses = list(by_id.values())

    if limit is not None:
        courses = courses[:limit]
    return courses


def run_scraper(
    output_dir: Path,
    courses_file: Optional[Path],
    discover: bool,
    limit: Optional[int],
    delay: float,
    headless: bool,
    course_ids: Optional[list[str]] = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[CourseRecord] = []

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = context.new_page()

        courses = load_courses_list(
            courses_file,
            page,
            discover=discover,
            limit=limit,
            course_ids=course_ids,
        )
        total = len(courses)

        for idx, item in enumerate(courses, start=1):
            cid = item["course_id"]
            hint = item["hint_text"]
            print(f"[{idx}/{total}] Processing course {cid} ...")

            try:
                info = parse_course_page(page, cid, hint)
            except PlaywrightTimeoutError:
                print(f"  ! Timeout loading course page {cid}, trying direct archive PDF...")
                info = {
                    "course_id": cid,
                    "title": hint or f"NPTEL_Course_{cid}",
                    "year": extract_year(hint),
                    "instructor": "",
                    "institute": "",
                    "duration": "",
                    "course_url": COURSE_DETAIL_URL.format(course_id=cid),
                    "pdf_links": [ARCHIVE_PDF_URL.format(course_id=cid)],
                    "details_body": "",
                    "lessons_body": "",
                }

            year = str(info["year"])
            title = str(info["title"])
            base_stem = sanitize_filename(f"{year} - {title}")

            saved_path: Optional[Path] = None
            details_path: Optional[Path] = None
            lessons_path: Optional[Path] = None
            source_url = ""
            fmt = "failed"

            for pdf_url in info["pdf_links"]:  # type: ignore[attr-defined]
                candidate = unique_filepath(output_dir, base_stem, "pdf", cid)
                if download_pdf(pdf_url, candidate):
                    saved_path = candidate
                    source_url = pdf_url
                    fmt = "pdf"
                    print(f"  -> Saved PDF: {candidate.name}")
                    break

            if saved_path is None and info["details_body"]:
                candidate = unique_filepath(output_dir, f"{base_stem} - Syllabus", "md", cid)
                write_markdown_syllabus(candidate, info)
                saved_path = candidate
                source_url = str(info["course_url"])
                fmt = "md"
                print(f"  -> Saved Syllabus MD: {candidate.name}")

            if info["details_body"]:
                details_path = unique_filepath(output_dir, f"{base_stem} - Details", "md", cid)
                write_details_markdown(details_path, info)
                print(f"  -> Saved Details MD: {details_path.name}")

            if info["lessons_body"]:
                lessons_path = unique_filepath(output_dir, f"{base_stem} - Lessons", "md", cid)
                write_lessons_markdown(lessons_path, info)
                print(f"  -> Saved Lessons MD: {lessons_path.name}")

            records.append(
                CourseRecord(
                    course_id=cid,
                    title=title,
                    year=year,
                    instructor=str(info["instructor"]),
                    institute=str(info["institute"]),
                    duration=str(info["duration"]),
                    course_url=str(info["course_url"]),
                    syllabus_format=fmt,
                    syllabus_source_url=source_url,
                    saved_file=str(saved_path.name) if saved_path else "",
                    details_file=str(details_path.name) if details_path else "",
                    lessons_file=str(lessons_path.name) if lessons_path else "",
                )
            )
            time.sleep(delay)

        browser.close()

    save_metadata(output_dir, records, merge_existing=bool(course_ids))
    pdf_count = sum(1 for r in records if r.syllabus_format == "pdf")
    md_count = sum(1 for r in records if r.syllabus_format == "md")
    fail_count = sum(1 for r in records if r.syllabus_format == "failed")
    print(
        f"\nDone! Processed {len(records)} courses: "
        f"{pdf_count} PDFs, {md_count} Markdown files, {fail_count} failed."
    )
    print(f"Output directory: {output_dir.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape NPTEL Computer Science & Engineering courses and syllabi."
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("nptel_cse_syllabi"),
        help="Directory to save syllabi and metadata (default: nptel_cse_syllabi)",
    )
    parser.add_argument(
        "--courses-file",
        type=Path,
        default=Path("cse_courses.json"),
        help="Path to JSON list of CSE courses (default: cse_courses.json)",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Also scrape https://nptel.ac.in/courses to discover additional 106* courses",
    )
    parser.add_argument(
        "--course-ids",
        nargs="+",
        default=None,
        help="Optional list of specific course IDs to (re-)scrape and merge into metadata",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max number of courses to scrape (useful for testing)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between course requests (default: 0.5)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Chromium in headed (visible) mode for debugging",
    )
    args = parser.parse_args()
    run_scraper(
        output_dir=args.output,
        courses_file=args.courses_file,
        discover=args.discover,
        limit=args.limit,
        delay=args.delay,
        headless=not args.headed,
        course_ids=args.course_ids,
    )


if __name__ == "__main__":
    main()
