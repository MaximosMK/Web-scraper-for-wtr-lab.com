# WTR-LAB Chapter Scraper

A Python-based desktop application with a PySide6 GUI for scraping novel chapters from wtr-lab.com. It allows users to download a range of chapters, save them into text files, and manage scraping configurations.

## Features

*   **Graphical User Interface (GUI):** Easy-to-use interface built with PySide6.
*   **Chapter Range Selection:** Specify start and end chapters for scraping.
*   **Batch Saving:** Scraped chapters are saved into text files, grouped by a configurable batch size.
*   **Automatic Retries:** Configurable retries for failed chapter/page fetches with delays.
*   **Pagination Handling:** Automatically navigates through multiple pages within a single chapter.
*   **Content Cleaning:**
    *   Removes duplicated titles from chapter content.
    *   Allows users to specify custom text lines to be removed from scraped content.
    *   Attempts to handle and mark incomplete or missing content.
*   **Configuration Profiles:** Save and load different scraping settings (URL, chapter range, output, etc.) as named profiles.
*   **Detailed Logging:** Real-time logging of the scraping process, including errors and warnings, displayed in the GUI.
*   **Progress Tracking:** Shows overall progress, current chapter status, and estimated time remaining.
*   **Summary File:** Generates a `_summary.json` file detailing successful and failed chapters.
*   **Headless Chrome:** Uses Selenium with a headless Chrome browser for scraping.
*   **Dark Theme:** Includes a custom dark theme for the GUI.

## Prerequisites

*   **Python:** Python 3.9 or newer is recommended.
*   **Chrome Browser:** A recent version of Google Chrome browser installed.
*   **`chromedriver.exe`:**
    *   You need `chromedriver.exe` that **matches your installed Google Chrome browser version**.
    *   Download it from the official "Chrome for Testing" availability page: https://googlechromelabs.github.io/chrome-for-testing/
    *   Place the `chromedriver.exe` file in the project directory (alongside `scraper.py`), or ensure it's in a directory included in your system's PATH environment variable.

## Setup Instructions

1.  **Get the Files:**
    *   Download or copy all project files (especially `scraper.py` and `requirements.txt`) into a local directory. This will be your project directory (e.g., you might name it `wtr_novel`).

2.  **Create a Virtual Environment:**
    *   Open PowerShell or Command Prompt in your project directory (e.g., `wtr_novel/`).
    *   Run:
        ```bash
        python -m venv .venv
        ```

3.  **Activate the Virtual Environment:**
    *   **PowerShell (Windows):**
        ```powershell
        .\.venv\Scripts\Activate.ps1
        ```
        (If you encounter execution policy issues, you might need to run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process` first in that PowerShell session.)
    *   **Command Prompt (Windows):**
        ```batch
        .\.venv\Scripts\activate.bat
        ```
    *   **Linux/macOS:**
        ```bash
        source .venv/bin/activate
        ```
    *   You should see `(.venv)` at the beginning of your terminal prompt.

4.  **Install Dependencies:**
    *   With the virtual environment active, run:
        ```bash
        python -m pip install -r requirements.txt
        ```

## How to Run

1.  Ensure your virtual environment is activated.
2.  Navigate to the project directory (e.g., `wtr_novel/`) in your terminal.
3.  Run the script:
    ```bash
    python scraper.py
    ```

## Using the Application

*   **Sample Chapter URL:** Enter the full URL of any chapter from the wtr-lab.com novel series you want to scrape. The application will attempt to extract the base URL pattern. Click "Test" to verify.
*   **Chapter Range:** Specify the "Start" and "End" chapter numbers.
*   **Output File Prefix:** Enter a name (e.g., "MyNovel") that will be used as a prefix for the output text files and the summary JSON file.
*   **Output Directory:** Click "Browse" to select a folder where the scraped files will be saved.
*   **Configuration Profiles:**
    *   **Profile Name:** Enter a name to save the current settings.
    *   **Select Profile:** Choose a saved profile from the dropdown.
    *   **Save/Load/Delete:** Manage your saved configurations.
*   **Fine-Tuning:**
    *   **Batch Size:** Number of chapters to group into a single output `.txt` file.
    *   **Advanced Options (Max Retries, Delay):** Configure how many times the scraper should retry a failed chapter/page and the delay (in seconds) between attempts.
    *   **Content Cleaning:** Enter specific lines of text (one per line) that you want to be completely removed from the scraped chapter content.
*   **Controls:**
    *   **Start Scraping:** Begins the scraping process.
    *   **Stop Scraping:** Gracefully stops the current scraping process.
    *   **Clear Inputs:** Resets all input fields to their default values.
    *   **Clear Log:** Clears the messages in the log area.
    *   **Open Output Folder:** Opens the selected output directory in your file explorer (enabled after selecting a directory or after a scrape).
*   **Progress & Log:**
    *   The progress bar shows the overall scraping progress.
    *   "Current Chapter Status" and "Estimated Time Remaining" provide real-time updates.
    *   The text area at the bottom displays detailed log messages.

## Output Files

*   **Chapter Files:** Scraped chapters are saved as `.txt` files in the specified output directory. Filenames will be in the format `[Output File Prefix]_[start_chapter]-[end_chapter].txt` (e.g., `MyNovel_1-10.txt`).
*   **Summary File:** A JSON file named `[Output File Prefix]_summary.json` (e.g., `MyNovel_summary.json`) is saved in a `summary` sub-directory within the script's folder. This file contains details about the scraping session, including total chapters attempted, successful count, failed count, and a list of results for each chapter.

## Troubleshooting

*   **`ModuleNotFoundError`:** Ensure you have activated the virtual environment and installed all packages from `d:\web\wtr_novel\requirements.txt` using `python -m pip install -r d:\web\wtr_novel\requirements.txt`.
*   **`ModuleNotFoundError`:** Ensure you have activated the virtual environment and installed all packages from `requirements.txt` using `python -m pip install -r requirements.txt`.
*   **`chromedriver.exe` errors:**
    *   "Message: session not created: This version of ChromeDriver only supports Chrome version X" - Your `chromedriver.exe` version does not match your Chrome browser version. Download the correct one.
    *   "chromedriver executable needs to be in PATH" - Make sure `chromedriver.exe` is in the project directory (alongside `scraper.py`) or in your system PATH.
*   **Scraping Failures/No Content:**
    *   The website structure of wtr-lab.com might have changed, requiring updates to the scraping logic (CSS selectors, etc.) in `scraper.py`.
    *   Your internet connection might be unstable, or the website might be temporarily down or blocking requests.
    *   Check the log for specific error messages from Selenium or the script.

## License

This project is currently unlicensed. Feel free to use and modify it for personal use.

## Author

Maximos (karouchmohamed21@gmail.com) (via assistance)