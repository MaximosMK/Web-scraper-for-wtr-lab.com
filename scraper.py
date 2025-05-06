import requests
from bs4 import BeautifulSoup
import json
import time
import os
import sys
import shutil
import subprocess
import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
# --- Import StaleElementReferenceException ---
from selenium.common.exceptions import NoSuchElementException, TimeoutException, StaleElementReferenceException

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QGridLayout, QLabel, QLineEdit,
                               QPushButton, QTextEdit, QProgressBar, QGroupBox, QSplitter,
                               QFileDialog, QMessageBox, QSizePolicy, QStyle, QComboBox,
                               QScrollArea)
# --- Added QIntValidator, QDoubleValidator ---
from PySide6.QtGui import QColor, QTextCharFormat, QIntValidator, QDoubleValidator
# --- Added QTimer ---
from PySide6.QtCore import QThread, Signal, Slot, QSettings, QCoreApplication, Qt, QTime, QMetaObject, QTimer
from PySide6.QtGui import QTextCursor
import re

from thefuzz import fuzz # Import fuzzy matching
# --- Severity Levels for Logging ---
INFO = 0
WARNING = 1
ERROR = 2
CRITICAL = 3


# --- Worker Thread for Scraping ---

class ScrapingWorker(QThread):
    # Modified signal to include severity level (int)
    log_message = Signal(str, int)
    progress_updated = Signal(int)
    chapter_scraped = Signal(str, str, int)
    saving_error = Signal(str)
    critical_error = Signal(str)
    finished = Signal()
    current_chapter_status = Signal(str) # Signal for detailed status updates
    scrape_summary = Signal(int, list) # Signal to send summary data
    estimated_time_updated = Signal(str)


    def __init__(self, base_url_pattern, overall_start_chapter, overall_end_chapter, batch_size, base_filename, output_directory, max_retries, delay_between_attempts, cleaning_patterns):
        super().__init__()
        self.base_url_pattern = base_url_pattern
        self.overall_start_chapter = overall_start_chapter
        self.overall_end_chapter = overall_end_chapter
        self.batch_size = batch_size
        self.base_filename = base_filename
        self.output_directory = output_directory
        self.max_retries = max_retries
        self.delay_between_attempts = delay_between_attempts
        self.cleaning_patterns = cleaning_patterns # Store cleaning patterns
        self._is_running = True
        self.failed_chapters = []
        self.successful_chapters_count = 0
        self._start_time = None
        self.successful_content = {} # Store {chapter_num: (title, content)}
        self.scrape_results = [] # List to store detailed results


    def run(self):
        """The main logic that runs in the separate thread."""
        self._start_time = time.time()
        driver = None
        try:
            self.log_message.emit(f"Scraping chapters {self.overall_start_chapter} to {self.overall_end_chapter} in batches of {self.batch_size}...", INFO)

            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu') # Add this line to disable GPU acceleration
            chrome_options.add_argument('--log-level=3') # Suppress INFO/WARNING messages from Chrome
            chrome_options.add_argument('--disable-software-rasterizer') # Add this
            chrome_options.add_argument('--disable-features=VizDisplayCompositor') # Add this
            chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')

            self.log_message.emit("Starting Chrome browser...", INFO)
            driver = webdriver.Chrome(options=chrome_options)

            chapters_processed_count = 0
            total_chapters_to_scrape = self.overall_end_chapter - self.overall_start_chapter + 1


            for batch_index, batch_start_chapter in enumerate(range(self.overall_start_chapter, self.overall_end_chapter + 1, self.batch_size)):
                if not self._is_running: break

                batch_end_chapter = min(batch_start_chapter + self.batch_size - 1, self.overall_end_chapter)
                chapters_in_batch = batch_end_chapter - batch_start_chapter + 1
                current_batch_number = batch_index + 1
                total_batches = (total_chapters_to_scrape + self.batch_size - 1) // self.batch_size


                self.log_message.emit(f"\n--- Scraping batch {current_batch_number} of {total_batches}: Chapters {batch_start_chapter} to {batch_end_chapter} ---", INFO)

                batch_content = []


                for i, chapter_num in enumerate(range(batch_start_chapter, batch_end_chapter + 1)):
                    if not self._is_running: break

                    self.current_chapter_status.emit(f"Batch {current_batch_number} of {total_batches}, Chapter {chapter_num} ({i + 1} of {chapters_in_batch} in batch)...")

                    # --- Append Google Translate parameter ---
                    chapter_url = f"{self.base_url_pattern}{chapter_num}?service=google"
                    title, content = self.scrape_single_chapter(driver, chapter_url, chapter_num,
                                                                max_retries=self.max_retries,
                                                                delay_between_attempts=self.delay_between_attempts,
                                                                cleaning_patterns=self.cleaning_patterns) # Pass patterns

                    # Check if substantial content was actually scraped
                    # Check includes handling potential None return, "Content Not Found" marker, and empty strings after stripping markers/whitespace.
                    is_content_found = content and \
                                       "Content Not Found" not in content and \
                                       len(content.replace("\n\n--- Page Break ---\n\n", "").replace("\n\n--- Incomplete Chapter ---\n\n", "").strip()) > 0

                    if is_content_found:
                        self.log_message.emit(f"  Successfully scraped: {title}", INFO)
                        # Add full content including title
                        # batch_content.append(f"{title}\n\n{content}\n\n") # Don't append here
                        self.successful_content[chapter_num] = (title, content) # Store content
                        self.chapter_scraped.emit(title, content, chapter_num) # Signal might be used for detailed GUI updates later
                        self.scrape_results.append({"chapter": chapter_num, "status": "success", "title": title, "output_file": None}) # Placeholder for filename
                        self.successful_chapters_count += 1
                    else:
                        # Handle cases where content wasn't found or was marked incomplete/empty
                        if "Content Not Found" in content:
                            log_msg = f"  Content not found for chapter {chapter_num} ({title}). Adding to failed list."
                            log_level = WARNING
                        elif "Incomplete Chapter" in content: # Check for the marker added by scrape_single_chapter
                             log_msg = f"  Scraping incomplete for chapter {chapter_num} ({title}). Adding to failed list, saving partial content."
                             log_level = WARNING
                             # Still add the partial content to the batch file
                             # batch_content.append(f"{title}\n\n{content}\n\n") # Don't append here
                             self.successful_content[chapter_num] = (title, content) # Store partial content too
                        else: # General failure or empty content after scraping attempts
                            log_msg = f"  Failed to scrape substantial content for chapter {chapter_num} ({title}). Adding to failed list."
                            log_level = ERROR

                        self.log_message.emit(log_msg, log_level)
                        # Add placeholder to batch file only if it wasn't partial content that got saved above
                        if "Incomplete Chapter" not in content:
                             pass # Don't add placeholder content here
                        self.scrape_results.append({"chapter": chapter_num, "status": "failed", "title": title, "url": chapter_url})
                        self.failed_chapters.append(chapter_num)


                    chapters_processed_count += 1
                    self.progress_updated.emit(chapters_processed_count)



                # Update estimated time after each chapter is processed
                elapsed_time = time.time() - self._start_time
                if chapters_processed_count > 0:
                    time_per_chapter = elapsed_time / chapters_processed_count
                    remaining_chapters = total_chapters_to_scrape - chapters_processed_count
                    estimated_remaining_time = time_per_chapter * remaining_chapters
                    self.estimated_time_updated.emit(f"Estimated Time Remaining: {self.format_time(estimated_remaining_time)}")


                # Add a small delay between scraping chapters within a batch if needed
                # time.sleep(0.5) # Already have delay_between_attempts after the batch save

                if not self._is_running: break # Check again after saving batch
                time.sleep(self.delay_between_attempts)

            # --- Automatic Retry Phase ---
            if self.failed_chapters and self._is_running:
                self.log_message.emit(f"\n--- Starting retry phase for {len(self.failed_chapters)} failed chapters ---", INFO)
                chapters_to_retry = list(self.failed_chapters) # Create a copy to iterate over
                retried_count = 0

                for chapter_num in chapters_to_retry:
                    if not self._is_running:
                        self.log_message.emit("Stop requested during retry phase.", WARNING)
                        break

                    self.current_chapter_status.emit(f"Retrying Chapter {chapter_num}...")
                    self.log_message.emit(f"  Retrying chapter {chapter_num}...", INFO)

                    chapter_url = f"{self.base_url_pattern}{chapter_num}?service=google"
                    title, content = self.scrape_single_chapter(driver, chapter_url, chapter_num,
                                                                max_retries=self.max_retries, # Use the same retry settings
                                                                delay_between_attempts=self.delay_between_attempts,
                                                                cleaning_patterns=self.cleaning_patterns) # Pass patterns

                    is_content_found = content and \
                                       "Content Not Found" not in content and \
                                       len(content.replace("\n\n--- Page Break ---\n\n", "").replace("\n\n--- Incomplete Chapter ---\n\n", "").strip()) > 0

                    if is_content_found:
                        self.log_message.emit(f"    Successfully retried: {title}", INFO)
                        self.successful_content[chapter_num] = (title, content) # Store retried content
                        if chapter_num in self.failed_chapters: # Check if still present before removing
                            self.failed_chapters.remove(chapter_num)

                        # Update the result status for this chapter
                        target_filepath = None # Output file will be determined later
                        for result in self.scrape_results:
                            if result["chapter"] == chapter_num:
                                result["status"] = "retried_success" # Mark as retried
                                result["output_file"] = target_filepath # Point to the batch file it was appended to
                        self.successful_chapters_count += 1 # Increment overall success count
                        retried_count += 1
                        # Optionally update progress bar slightly? Or just log.
                        # self.progress_updated.emit(chapters_processed_count + retried_count) # This might exceed max value, maybe better not to update progress here
                    else:
                        # Log failure again, maybe with less detail
                        self.log_message.emit(f"    Retry failed for chapter {chapter_num} ({title}).", WARNING)

                    time.sleep(self.delay_between_attempts) # Delay between retries


            # --- End of Automatic Retry Phase ---


        except Exception as e:
            self.log_message.emit(f"\nAn unexpected error occurred during the scraping process: {e}", CRITICAL)
            self.critical_error.emit(f"An unexpected error occurred during scraping: {e}. See log for details.")

        finally:
            if driver:
                self.log_message.emit("Entering finally block, attempting to close driver...", INFO)
                self.log_message.emit("\nClosing Chrome browser...", INFO)
                driver.quit()
                self.log_message.emit("Driver quit successfully.", INFO)
            else:
                self.log_message.emit("Entering finally block, driver was not active.", INFO)

            # --- Save Summary JSON ---
            self.log_message.emit("Attempting to save summary JSON...", INFO)
            # --- Define and create summary directory ---
            script_dir = os.path.dirname(__file__) # Get directory of the script
            summary_dir = os.path.join(script_dir, 'summary')
            os.makedirs(summary_dir, exist_ok=True)
            summary_filename = f"{self.base_filename}_summary.json"
            safe_summary_filename = re.sub(r'[\\/:*?"<>|]', '_', summary_filename) # Use base_filename from input for summary
            summary_filepath = os.path.join(summary_dir, safe_summary_filename) # Save in summary_dir
            summary_data = {
                "total_chapters_attempted": self.overall_end_chapter - self.overall_start_chapter + 1,
                "successful_count": self.successful_chapters_count,
                "failed_count": len(self.failed_chapters),
                "results": sorted(self.scrape_results, key=lambda x: x['chapter']) # Sort results by chapter number
            }
            try:
                with open(summary_filepath, 'w', encoding='utf-8') as f:
                    json.dump(summary_data, f, indent=4)
                self.log_message.emit(f"Saved scrape summary to {summary_filepath}", INFO)
            except Exception as e:
                self.log_message.emit(f"Error saving summary JSON to {summary_filepath}: {e}", ERROR)
            self.log_message.emit("Finished saving summary JSON.", INFO)
            # --- End Save Summary JSON ---

            # Emit scrape summary data
            self.log_message.emit("Emitting scrape_summary signal...", INFO)
            self.scrape_summary.emit(self.successful_chapters_count, self.failed_chapters) # Keep summary signal
            self.estimated_time_updated.emit("Estimated Time Remaining: N/A")

            self.log_message.emit("Emitting finished signal...", INFO)
            self.log_message.emit("\n--- Scraping process finished ---", INFO)
            self.finished.emit()


    @Slot()
    def stop(self):
        """Slot to be called from the main thread to stop the worker."""
        self.log_message.emit("Stop signal received. Attempting graceful shutdown...", INFO)
        self._is_running = False

    def _clean_title_prefix(self, title_str):
        """Removes common prefixes like 'Chapter X:', '#X', etc. for comparison."""
        if not title_str: return ""
        cleaned = title_str.strip()
        # Define patterns once
        patterns = [
            r'^#\s*\d+\s*',                 # Matches #123 at the start
            r'^Chapter\s*\d+\s*[:\-–—]\s*', # Matches Chapter 123: or Chapter 123 -
            r'^\d+\s*[:\-–—]\s*',           # Matches 123 -
            r'^Chapter\s*\d+\s+',           # Matches Chapter 123 followed by space
            r'^\d+\s+',                     # Matches 123 followed by space (e.g., "21 Title")
        ]
        # Loop until no pattern makes a change in a full pass
        while True:
            previous_cleaned = cleaned
            for pattern in patterns:
                # Apply each pattern once per outer loop iteration
                cleaned = re.sub(pattern, '', cleaned, count=1, flags=re.IGNORECASE).strip()
            # If no changes were made in this full pass, break
            if cleaned == previous_cleaned:
                break
        return cleaned

    # --- Corrected scrape_single_chapter with pagination handling ---
    def scrape_single_chapter(self, driver, url, chapter_num, max_retries, delay_between_attempts, cleaning_patterns):
        """
        Scrapes a single chapter, including handling pagination within the chapter.
        Returns the chapter title and concatenated content.
        """
        chapter_title_text = "Title Not Found"
        all_chapter_content = []
        current_url = url # Start with the initial chapter URL
        page_number = 1 # Track page number within the chapter
        chapter_fully_scraped = False # Flag to indicate if all pages were successfully scraped


        while self._is_running: # Outer loop for iterating through pages
            page_content = None # Content for the current page
            page_successfully_loaded = False # Flag to indicate if the current page was loaded successfully after retries
            next_page_link = None # Reset for each page iteration


            for attempt in range(1, max_retries + 1): # Inner loop for retrying the current page load
                if not self._is_running: break # Stop if requested during retries

                try:
                    self.log_message.emit(f"  Attempt {attempt}/{max_retries} for chapter {chapter_num} (Page {page_number}: {current_url})...", INFO)
                    driver.get(current_url)

                    try:
                        # Wait for chapter body to load
                        chapter_body_container = WebDriverWait(driver, 20).until(
                            EC.presence_of_element_located((By.CLASS_NAME, 'chapter-body'))
                        )

                        # --- Wait for actual content (e.g., a paragraph) to appear ---
                        try:
                            # --- Wait for ANY text to be present in the container ---
                            WebDriverWait(driver, 20).until( # Increased wait slightly to 20s
                                EC.text_to_be_present_in_element((By.CLASS_NAME, 'chapter-body'), '.') # Wait for any char '.'
                            )
                            self.log_message.emit(f"    Content paragraph appeared for chapter {chapter_num} (Page {page_number}).", INFO)

                            # --- Explicitly wait for placeholder HTML to disappear (Loop Check) ---
                            max_placeholder_checks = 12 # Check up to 12 times (e.g., 12 * 0.25s = 3 seconds)
                            for check_count in range(max_placeholder_checks):
                                try:
                                    # --- Re-find the element inside the loop to avoid staleness ---
                                    current_container = driver.find_element(By.CLASS_NAME, 'chapter-body')
                                    inner_html = current_container.get_attribute('innerHTML')
                                # --- Add specific handling for StaleElementReferenceException ---
                                except StaleElementReferenceException:
                                    self.log_message.emit(f"    StaleElementReferenceException during placeholder check loop. Assuming element changed.", WARNING)
                                    break # Exit the placeholder check loop
                                except NoSuchElementException: # Keep existing NoSuchElementException handling
                                    self.log_message.emit(f"    Chapter body container disappeared during placeholder check loop.", WARNING); break
                                if 'placeholder-glow' not in inner_html:
                                    break
                                time.sleep(0.25) # Wait a bit before checking again
                            else: # If loop finishes without break
                                self.log_message.emit(f"    Placeholder HTML might still be present after extra wait for chapter {chapter_num} (Page {page_number}).", WARNING)
                            # --- End Placeholder Loop Check ---

                        except TimeoutException:
                            self.log_message.emit(f"    Timed out waiting for content paragraph to appear for chapter {chapter_num} (Page {page_number}). Content might be missing or still loading.", WARNING)
                        page_successfully_loaded = True # Mark page as loaded even if placeholder didn't disappear (might still have content)
                    except TimeoutException:
                        self.log_message.emit(f"  Timed out waiting for chapter-body container on {current_url} for chapter {chapter_num}.", WARNING)
                        page_successfully_loaded = False # Failed to find the main container
                    except Exception as e:
                         self.log_message.emit(f"  Error loading page {page_number} on attempt {attempt}: {e}", ERROR)
                         page_successfully_loaded = False


                    if page_successfully_loaded:
                         page_source = driver.page_source
                         soup = BeautifulSoup(page_source, 'html.parser')

                         # Extract Title (only need this from the first page)
                         # --- Prioritize H3 title, then breadcrumb, clean immediately ---
                         if page_number == 1:
                             title_element = soup.find('h3', class_='chapter-title')
                             if title_element:
                                 # Clean the H3 title immediately
                                 chapter_title_text = self._clean_title_prefix(title_element.get_text(strip=True))
                             elif chapter_title_text == "Title Not Found": # Check if still not found
                                 # Fallback to breadcrumb if H3 not found
                                 breadcrumb_title_element = soup.select_one('.breadcrumb-item.active')
                                 if breadcrumb_title_element:
                                     breadcrumb_text = breadcrumb_title_element.get_text(strip=True)
                                     # Try cleaning the breadcrumb text too
                                     chapter_title_text = self._clean_title_prefix(breadcrumb_text)
                             # If still not found after both, it remains "Title Not Found"

                             self.log_message.emit(f"    Extracted/Cleaned Title (Page 1): '{chapter_title_text}'", INFO) # Log the final title used


                         # Extract Content for the current page
                         # --- Find container initially for checks ---
                         content_container_initial = soup.find('div', class_='chapter-body')

                         if content_container_initial:
                             # --- Re-find container right before text extraction ---
                             soup_refind = BeautifulSoup(page_source, 'html.parser') # Re-parse (quick)
                             content_container = soup_refind.find('div', class_='chapter-body')
                             if not content_container: # If it disappeared between checks
                                 self.log_message.emit(f"    Content container disappeared between initial check and text extraction on Page {page_number}.", WARNING)
                                 page_content = "Content Not Found (Container Vanished)"
                             else:
                                 # --- Attempt to remove duplicated title element from within content ---
                                 inner_title_element = content_container.find('h3') # Try finding h3 first
                                 if inner_title_element:
                                     # Optional: Check if its text roughly matches the extracted title for more safety
                                     # if chapter_title_text != "Title Not Found" and chapter_title_text in inner_title_element.get_text(strip=True):
                                     self.log_message.emit(f"    Found and removing inner title element: {inner_title_element.get_text(strip=True)}", INFO)
                                     inner_title_element.extract() # Remove the element from the container
                                 # --- End removal attempt ---

                                 # Get all text nodes, preserving some structure with separators
                                 page_content = content_container.get_text(separator='\n', strip=True) # Use re-found container

                                 # --- Attempt to remove duplicated title from first line of content ---
                                 # Use a loop to remove potentially multiple title lines at the start
                                 if page_content and chapter_title_text != "Title Not Found":
                                     lines = page_content.split('\n')
                                     # Clean the main chapter title ONCE before the loop
                                     core_chapter_title = self._clean_title_prefix(chapter_title_text)
                                     removed_count = 0
                                     while lines: # Loop while there are lines left
                                         current_first_line_cleaned = lines[0].strip()
                                         if not current_first_line_cleaned: # Skip empty lines at the start
                                             lines.pop(0); continue

                                         # Remove potential prefix like '#21' before comparison
                                         # Use the helper function for cleaning
                                         core_first_line = self._clean_title_prefix(current_first_line_cleaned)

                                         # Compare core text: Use fuzzy matching after cleaning
                                         match_found = False
                                         if core_first_line and core_chapter_title:
                                             cl_lower = core_first_line.lower()
                                             ct_lower = core_chapter_title.lower()

                                             # Use token_set_ratio for flexibility with word order/minor diffs
                                             similarity_ratio = fuzz.token_set_ratio(cl_lower, ct_lower)

                                             # Set a threshold (e.g., 85). Adjust as needed.
                                             if similarity_ratio > 85: # Threshold for considering it a match
                                                 match_found = True
                                                 self.log_message.emit(f"    Fuzzy Match Success (Ratio: {similarity_ratio}): Line='{core_first_line}' | Title='{core_chapter_title}'", INFO)
                                         if match_found:
                                             self.log_message.emit(f"    Found and removing duplicated title line: {lines[0]}", INFO)
                                             lines.pop(0) # Remove the first line
                                             removed_count += 1
                                         else:
                                             break # Stop if the first line doesn't match
                                     if removed_count > 0:
                                         page_content = '\n'.join(lines) # Reassemble content only if lines were removed
                                 # --- End duplicated title removal loop ---
                                 # --- Check for AI Translation/Registration Block ---
                                 ai_block_keywords = ["AI Translation Requires Registration", "Sign up for free", "Google Translation"]
                                 if page_content and all(keyword in page_content for keyword in ai_block_keywords):
                                     self.log_message.emit(f"    Detected 'AI Translation Requires Registration' block on Page {page_number} ({current_url}). Treating as content not found.", WARNING)
                                     page_content = "Content Not Found (AI Translation Block)" # Specific marker
                                 # --- End AI Block Check ---

                                 if page_content: # Check if get_text actually returned something
                                     # Avoid logging success if it's the AI block marker
                                     if page_content != "Content Not Found (AI Translation Block)":
                                         self.log_message.emit(f"    Scraped content from Page {page_number} using get_text()", INFO)
                                     # No else needed here, the AI block case logs its own message above
                                 else:
                                     self.log_message.emit(f"    Content container found, but get_text() returned empty content on Page {page_number} ({current_url}).", WARNING)
                                     page_content = "Content Not Found (Container Empty)" # Explicitly mark as empty

                                 # --- Apply Cleaning Patterns ---
                                 if page_content and cleaning_patterns:
                                     lines = page_content.split('\n')
                                     cleaned_lines = []
                                     for line in lines:
                                         line_stripped = line.strip()
                                         if line_stripped not in cleaning_patterns: # Simple exact match (case-sensitive)
                                             cleaned_lines.append(line) # Keep original line with original whitespace
                                     page_content = '\n'.join(cleaned_lines)
                                 # --- End Apply Cleaning Patterns ---

                         else:
                             self.log_message.emit(f"    Content container not found on Page {page_number} ({current_url}).", WARNING)
                             page_content = "Content Not Found (Container Missing)"


                         # --- Check for Pagination Links ---
                         pagination_container = soup.select_one('.chapter-pager')
                         if pagination_container:
                             next_link_element = None
                             next_link_element = pagination_container.find('a', rel='next')
                             if not next_link_element:
                                  next_link_element = pagination_container.find('a', string=re.compile(r'next', re.IGNORECASE))
                             if not next_link_element:
                                 next_link_element = pagination_container.find('a', class_='pager-next')
                             if not next_link_element:
                                 next_link_element = pagination_container.find('a', string=re.compile(r'>>?'))


                             if next_link_element and 'href' in next_link_element.attrs:
                                 next_page_url_relative = next_link_element['href']
                                 if not next_page_url_relative.startswith('http'):
                                      scheme_netloc_match = re.match(r"(https?://[^/]+)", url)
                                      base_url_parts = scheme_netloc_match.group(1) if scheme_netloc_match else ""

                                      if base_url_parts and next_page_url_relative.startswith('/'):
                                           next_page_link = base_url_parts + next_page_url_relative
                                      else:
                                           base_path = url.rsplit('/', 1)[0]
                                           next_page_link = base_path + '/' + next_page_url_relative.lstrip('/')

                                 else:
                                      next_page_link = next_page_url_relative

                                 # --- Ensure Google Translate parameter is on next page link ---
                                 if 'service=google' not in next_page_link:
                                     if '?' in next_page_link:
                                         next_page_link += '&service=google'
                                     else:
                                         next_page_link += '?service=google'
                                 self.log_message.emit(f"    Found/Adjusted next page link: {next_page_link}", INFO)
                             else:
                                 self.log_message.emit(f"    No next page link found in pagination container on Page {page_number}. Assuming end of chapter.", INFO)
                         else:
                             self.log_message.emit(f"    No pagination container found on Page {page_number}. Assuming end of chapter.", INFO)


                         break # Break the retry loop if page loaded successfully


                    else:
                         self.log_message.emit(f"  Page {page_number} failed to load successfully after {attempt} attempts.", ERROR)
                         page_content = "Content Not Found (Page failed to load)"
                         next_page_link = None
                         if attempt == max_retries or not self._is_running:
                              break
                         else:
                              time.sleep(delay_between_attempts)
                              continue


                except Exception as e:
                    # Format the error message string first
                    error_msg = f"  Error scraping page {page_number} on attempt {attempt} for chapter {chapter_num}: {e}"
                    # --- Re-enable emit, root cause should be fixed ---
                    self.log_message.emit(error_msg, ERROR)
                    # --- End wrap ---
                    if attempt < max_retries and self._is_running:
                        time.sleep(delay_between_attempts)
                        continue
                    else:
                        self.log_message.emit(f"  Max retries reached or stop requested for chapter {chapter_num} (Page {page_number}: {current_url}). Could not scrape page content.", ERROR)
                        page_content = "Content Not Found (Scraping error)"
                        page_successfully_loaded = False
                        next_page_link = None
                        break


            if not self._is_running:
                 self.log_message.emit(f"  Stop requested. Stopping pagination for chapter {chapter_num}.", INFO)
                 break


            if page_content is not None:
                 all_chapter_content.append(page_content)
            else:
                 # This case should ideally not happen if page_content is always assigned a value
                 # but adding a log just in case.
                 self.log_message.emit(f"  Warning: page_content was None after retry loop for chapter {chapter_num}, page {page_number}.", WARNING)
                 pass # Don't append None


            if next_page_link and page_successfully_loaded:
                current_url = next_page_link
                page_number += 1
                time.sleep(delay_between_attempts) # Add delay between page loads within a chapter
            else:
                # Determine if the chapter scrape was fully successful
                if not page_successfully_loaded and not all_chapter_content:
                     # Failed to load even the first page
                     self.log_message.emit(f"  Could not load the first page ({url}) for chapter {chapter_num}. Chapter scrape failed.", ERROR)
                     chapter_fully_scraped = False
                elif page_successfully_loaded and next_page_link is None:
                     # Last page loaded successfully, and no 'next' link was found
                     self.log_message.emit(f"  Finished scraping chapter {chapter_num} (last page was {page_number}).", INFO)
                     chapter_fully_scraped = True
                else: # page_successfully_loaded is False OR next_page_link existed but we stopped (e.g., error, stop request)
                     self.log_message.emit(f"  Finished scraping chapter {chapter_num}, but the last page ({page_number}) had issues loading or finding the next link. Chapter scrape incomplete.", WARNING)
                     chapter_fully_scraped = False

                break # Exit the while loop (pagination loop)


        final_chapter_title = f"Chapter {chapter_num} - {chapter_title_text}"

        if all_chapter_content:
            full_content = "\n\n--- Page Break ---\n\n".join(all_chapter_content)
            # Check if the *combined* content (minus markers) is substantial
            # Added more markers to replace for the check
            if len(full_content.replace("\n\n--- Page Break ---\n\n", "")
                             .replace("Content Not Found (Container Empty)","")
                             .replace("Content Not Found (Container Missing)","")
                             .replace("Content Not Found (AI Translation Block)","")
                             .replace("Content Not Found (Page failed to load)","")
                             .replace("Content Not Found (Scraping error)","")
                             .strip()) > 0:
                 if chapter_fully_scraped:
                     return final_chapter_title, full_content
                 else:
                      # Append incomplete marker if not fully scraped but has some content
                      self.log_message.emit(f"  Returning partial content for chapter {chapter_num} due to incomplete scrape.", WARNING)
                      return final_chapter_title, full_content + "\n\n--- Incomplete Chapter ---\n\n"
            else:
                 # Scraped pages, but all were empty or had only markers
                 self.log_message.emit(f"  Scraped pages for chapter {chapter_num}, but no substantial content was found.", WARNING)
                 return final_chapter_title, "Content Not Found"
        else:
             # No pages were successfully scraped at all
             self.log_message.emit(f"  Could not scrape any content for chapter {chapter_num}.", ERROR)
             return final_chapter_title, "Content Not Found"


    def format_time(self, seconds):
        """Helper to format time in H:M:S."""
        if seconds is None:
            return "N/A"
        if seconds < 0:
             seconds = 0
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{int(h):02d}h {int(m):02d}m {int(s):02d}s"


# --- Main Application Window ---

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("WTR-LAB Chapter Scraper")
        # self.setGeometry(100, 100, 750, 800) # Remove fixed geometry, will set based on screen

        QCoreApplication.setOrganizationName("YourCompanyName")
        QCoreApplication.setApplicationName("WTRScraper")
        self.settings = QSettings()


        self.worker_thread = None
        self.worker = None

        self.input_widgets = []
        self.numeric_input_widgets = [] # Specific list for numeric fields

        # Define colors for log levels
        self.log_colors = {
            INFO: QColor("#cccccc"),     # Light gray for info
            WARNING: QColor("#ffcc00"),  # Yellow/Orange for warnings
            ERROR: QColor("#ff6600"),    # Orange/Red for errors
            CRITICAL: QColor("#ff3300")  # Bright Red for critical errors
        }

        # --- Style constants ---
        self.valid_style = "border: 1px solid #454545;"
        self.invalid_style = "border: 1px solid #d9534f;" # Use stop button red for invalid


        # --- Main Splitter Layout ---
        main_splitter = QSplitter(Qt.Vertical) # Vertical split
        self.setCentralWidget(main_splitter)

        # --- Top Panel Widget (Scrollable Area) ---
        scroll_area = QScrollArea() # Create a scroll area
        scroll_area.setWidgetResizable(True) # Allow the inner widget to resize
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff) # No horizontal scroll needed
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded) # Show vertical scroll only if needed

        # --- Widget INSIDE the Scroll Area ---
        left_panel_widget = QWidget()
        left_layout = QVBoxLayout(left_panel_widget)
        left_layout.setSpacing(15) # Add default spacing between widgets/layouts
        left_panel_widget.setObjectName("scrollAreaWidgetContents") # Give it an object name
        # left_panel_widget.setAutoFillBackground(True) # Keep or remove based on testing, QSS might override
        # You might need to explicitly set the palette color if autoFillBackground isn't enough with QSS
        left_layout.setContentsMargins(10,10,10,10) # Add some margins
        scroll_area.setWidget(left_panel_widget)

        # --- Bottom Panel (Log Area) ---
        # Log area will be added directly to the splitter as the second widget

        # --- Input Group Box (goes into left_layout) ---
        input_group_box = QGroupBox("Scraping Settings") # Create the group box
        # --- Use a QVBoxLayout for the main sections ---
        # input_layout = QGridLayout(input_group_box) # Remove Grid for main group
        # input_layout.setColumnStretch(1, 1) # Allow column 1 to stretch

        # --- Section 1: Core Inputs (Using a GridLayout for alignment) ---
        url_layout = QHBoxLayout() # Layout for URL entry and test button
        self.url_entry = QLineEdit()
        self.url_entry.setToolTip("Enter the URL of any chapter from the series.\nThe script will extract the base URL pattern (e.g., 'https://site.com/novel/chapter-').")
        self.url_entry.textChanged.connect(self.validate_url_input) # Connect validation
        url_layout.addWidget(self.url_entry, 1) # Allow URL entry to stretch
        self.test_url_button = QPushButton("Test")
        self.test_url_button.setToolTip("Check if the base URL pattern can be extracted.")
        self.test_url_button.clicked.connect(self.test_url_pattern)
        url_layout.addWidget(self.test_url_button)

        core_inputs_layout = QGridLayout()
        core_inputs_layout.setColumnStretch(1, 1) # Allow input column to stretch
        core_inputs_layout.addWidget(QLabel("Sample Chapter URL:"), 0, 0, alignment=Qt.AlignRight)
        core_inputs_layout.addLayout(url_layout, 0, 1)
        self.input_widgets.append(self.url_entry)

        # --- Extracted Base URL Label ---
        self.extracted_url_label = QLabel("Extracted Base URL: (Enter URL above)")
        self.extracted_url_label.setStyleSheet("font-style: italic; color: #909090;")
        self.extracted_url_label.setWordWrap(True)
        core_inputs_layout.addWidget(self.extracted_url_label, 1, 1) # Span 1 column now

        # Start Chapter
        chapter_range_layout = QHBoxLayout()
        self.start_chapter_entry = QLineEdit()
        # self.start_chapter_entry.setFixedWidth(100) # Remove fixed width, let grid handle
        self.start_chapter_entry.setToolTip("The first chapter number to scrape.")
        self.start_chapter_entry.setValidator(QIntValidator(1, 999999)) # Set validator
        self.start_chapter_entry.textChanged.connect(lambda: self.validate_numeric_input(self.start_chapter_entry, min_val=1)) # Connect validation
        chapter_range_layout.addWidget(QLabel("Start:"))
        chapter_range_layout.addWidget(self.start_chapter_entry)
        self.input_widgets.append(self.start_chapter_entry)
        self.numeric_input_widgets.append(self.start_chapter_entry)

        # End Chapter
        self.end_chapter_entry = QLineEdit()
        # self.end_chapter_entry.setFixedWidth(100) # Remove fixed width
        self.end_chapter_entry.setToolTip("The last chapter number to scrape.")
        self.end_chapter_entry.setValidator(QIntValidator(1, 999999)) # Set validator
        self.end_chapter_entry.textChanged.connect(lambda: self.validate_numeric_input(self.end_chapter_entry, min_val=1)) # Connect validation
        chapter_range_layout.addWidget(QLabel("End:"))
        chapter_range_layout.addWidget(self.end_chapter_entry)
        chapter_range_layout.addStretch(1) # Push start/end fields together
        self.input_widgets.append(self.end_chapter_entry)
        self.numeric_input_widgets.append(self.end_chapter_entry)

        core_inputs_layout.addWidget(QLabel("Chapter Range:"), 2, 0, alignment=Qt.AlignRight | Qt.AlignTop) # Align label top
        core_inputs_layout.addLayout(chapter_range_layout, 2, 1)

        # # Batch Size (Moved to Fine-tuning section)
        # self.batch_size_entry = QLineEdit()
        # self.batch_size_entry.setFixedWidth(100)
        # self.batch_size_entry.setToolTip("Number of chapters to save in each output text file.")
        # self.batch_size_entry.setValidator(QIntValidator(1, 9999)) # Set validator
        # self.batch_size_entry.textChanged.connect(lambda: self.validate_numeric_input(self.batch_size_entry, min_val=1)) # Connect validation
        # core_inputs_layout.addWidget(QLabel("Batch Size:"), 3, 0, alignment=Qt.AlignRight)
        # core_inputs_layout.addWidget(self.batch_size_entry, 3, 1, alignment=Qt.AlignLeft) # Align left
        # self.input_widgets.append(self.batch_size_entry)
        # self.numeric_input_widgets.append(self.batch_size_entry)

        # --- Output File Prefix (Relabeled) ---
        self.filename_entry = QLineEdit()
        self.filename_entry.setToolTip("The prefix for the output files (e.g., 'MyNovel').\nChapter ranges and extensions will be added automatically.")
        core_inputs_layout.addWidget(QLabel("Output File Prefix:"), 3, 0, alignment=Qt.AlignRight)
        core_inputs_layout.addWidget(self.filename_entry, 3, 1)
        self.input_widgets.append(self.filename_entry)

        # Output Directory
        self.output_dir_entry = QLineEdit()
        self.output_dir_entry.setReadOnly(True)
        self.output_dir_entry.setToolTip("The folder where scraped chapters and summary file will be saved.")
        # input_layout.addWidget(QLabel("Output Directory:"), 5, 0, alignment=Qt.AlignRight) # This was the error line
        output_dir_layout = QHBoxLayout() # Layout for entry + browse
        output_dir_layout.addWidget(self.output_dir_entry, 1) # Stretch entry

        self.browse_button = QPushButton("Browse")
        self.browse_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon)) # Add Icon
        self.browse_button.setToolTip("Select the output directory.")
        self.browse_button.clicked.connect(self.browse_output_directory)
        output_dir_layout.addWidget(self.browse_button)
        core_inputs_layout.addWidget(QLabel("Output Directory:"), 4, 0, alignment=Qt.AlignRight)
        core_inputs_layout.addLayout(output_dir_layout, 4, 1)

        # --- Advanced Options Group (Now always visible) ---
        self.advanced_options_group = QGroupBox("Advanced Options")
        # self.advanced_options_group.setCheckable(True) # Make it non-checkable
        # self.advanced_options_group.setChecked(False) # No longer needed
        advanced_layout = QGridLayout(self.advanced_options_group)

        # Max Retries
        self.max_retries_entry = QLineEdit()
        self.max_retries_entry.setFixedWidth(100)
        self.max_retries_entry.setToolTip("Number of times to retry fetching a chapter/page if it fails.")
        self.max_retries_entry.setValidator(QIntValidator(0, 99)) # Set validator (allow 0)
        self.max_retries_entry.textChanged.connect(lambda: self.validate_numeric_input(self.max_retries_entry, min_val=0)) # Connect validation
        advanced_layout.addWidget(QLabel("Max Retries:"), 0, 0, alignment=Qt.AlignRight)
        advanced_layout.addWidget(self.max_retries_entry, 0, 1)
        self.input_widgets.append(self.max_retries_entry)
        self.numeric_input_widgets.append(self.max_retries_entry)

        # Delay Between Attempts
        self.delay_entry = QLineEdit()
        self.delay_entry.setFixedWidth(100)
        self.delay_entry.setToolTip("Seconds to wait between chapter scraping attempts and after failed attempts.")
        double_validator = QDoubleValidator(0.0, 600.0, 2) # Allow 0.0 to 600.0, 2 decimal places
        double_validator.setNotation(QDoubleValidator.StandardNotation)
        self.delay_entry.setValidator(double_validator) # Set validator
        self.delay_entry.textChanged.connect(lambda: self.validate_numeric_input(self.delay_entry, min_val=0.0)) # Connect validation
        advanced_layout.addWidget(QLabel("Delay (sec):"), 0, 2, alignment=Qt.AlignRight)
        advanced_layout.addWidget(self.delay_entry, 0, 3)
        self.input_widgets.append(self.delay_entry)
        self.numeric_input_widgets.append(self.delay_entry)

        advanced_layout.setColumnStretch(4, 1) # Add stretch to push advanced options left

        # input_layout.addWidget(self.advanced_options_group, 6, 0, 1, 4) # Add advanced group to main input layout
        # left_layout.addWidget(input_group_box) # Add input group to left layout
        left_layout.addLayout(core_inputs_layout) # Add core inputs grid to main VBox
        left_layout.addSpacing(20) # Add space after core inputs

        # --- Section 2: Configuration Profiles ---
        # --- Content Cleaning Group Box ---
        cleaning_group_box = QGroupBox("Content Cleaning")
        cleaning_layout = QVBoxLayout(cleaning_group_box)

        cleaning_layout.addWidget(QLabel("Remove lines exactly matching (one per line):"))
        self.cleaning_patterns_edit = QTextEdit()
        self.cleaning_patterns_edit.setToolTip("Enter text lines to be completely removed from the scraped content.\nEach line you enter here will be matched exactly (case-sensitive).\nExample: 'Translated by XYZ'\nExample: 'Please support the author!'")
        self.cleaning_patterns_edit.setAcceptRichText(False) # Ensure plain text
        # self.cleaning_patterns_edit.setFixedHeight(100) # Remove fixed height
        cleaning_layout.addWidget(self.cleaning_patterns_edit)
        self.input_widgets.append(self.cleaning_patterns_edit) # Add to input widgets

        # left_layout.addWidget(cleaning_group_box) # Add cleaning group to left layout (Moved)

        # --- Configuration Management ---
        config_group_box = QGroupBox("Configuration Profiles")
        config_layout = QGridLayout(config_group_box) # Use GridLayout for better alignment

        # --- Profile Name (Moved from Scraping Settings) ---
        config_layout.addWidget(QLabel("Profile Name:"), 0, 0, alignment=Qt.AlignRight)
        self.profile_name_entry = QLineEdit() # Renamed from novel_name_entry
        self.profile_name_entry.setToolTip("Enter a name to save or load the current settings.")
        config_layout.addWidget(self.profile_name_entry, 0, 1, 1, 3)
        self.input_widgets.append(self.profile_name_entry) # Add to input widgets for enable/disable

        # Profile Selection Dropdown
        config_layout.addWidget(QLabel("Select Profile:"), 1, 0, alignment=Qt.AlignRight)
        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip("Select a saved profile to load or delete.")
        self.profile_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred) # Allow combo to expand
        self.profile_combo.activated.connect(self.load_selected_profile_from_combo) # Load when item selected
        config_layout.addWidget(self.profile_combo, 1, 1, 1, 3)

        # Profile Buttons
        profile_button_layout = QHBoxLayout()
        self.save_config_button = QPushButton("Save") # Shortened name
        self.save_config_button.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.save_config_button.setToolTip("Save the current settings using the 'Profile Name' field above.")
        self.save_config_button.clicked.connect(self.save_config_profile)
        profile_button_layout.addWidget(self.save_config_button)

        self.load_config_button = QPushButton("Load") # Shortened name
        self.load_config_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.load_config_button.setToolTip("Load settings from the profile selected in the dropdown.")
        self.load_config_button.clicked.connect(self.load_selected_profile_from_combo) # Connect to same load function
        profile_button_layout.addWidget(self.load_config_button)

        self.delete_config_button = QPushButton("Delete") # Shortened name
        self.delete_config_button.setToolTip("Delete the profile selected in the dropdown.")
        self.delete_config_button.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.delete_config_button.clicked.connect(self.delete_config_profile)
        profile_button_layout.addWidget(self.delete_config_button)
        profile_button_layout.addStretch(1) # Push buttons left

        config_layout.addLayout(profile_button_layout, 2, 1, 1, 3) # Add button layout

        left_layout.addWidget(config_group_box) # Add config group to left layout
        left_layout.addSpacing(20) # Add space after config

        # --- Section 3: Fine-Tuning (Batch Size, Advanced, Cleaning) ---
        tuning_group_box = QGroupBox("Fine-Tuning")
        tuning_layout = QVBoxLayout(tuning_group_box)

        # Batch Size (Moved here)
        batch_size_layout = QHBoxLayout()
        batch_size_layout.addWidget(QLabel("Batch Size:"))
        self.batch_size_entry = QLineEdit()
        self.batch_size_entry.setFixedWidth(80) # Slightly smaller fixed width
        self.batch_size_entry.setToolTip("Number of chapters to save in each output text file.")
        self.batch_size_entry.setValidator(QIntValidator(1, 9999)) # Set validator
        self.batch_size_entry.textChanged.connect(lambda: self.validate_numeric_input(self.batch_size_entry, min_val=1)) # Connect validation
        batch_size_layout.addWidget(self.batch_size_entry)
        batch_size_layout.addStretch(1) # Push to left
        tuning_layout.addLayout(batch_size_layout)
        self.input_widgets.append(self.batch_size_entry)
        self.numeric_input_widgets.append(self.batch_size_entry)

        # Advanced Options (Retries/Delay - Moved here)
        tuning_layout.addWidget(self.advanced_options_group)

        # Cleaning Patterns (Moved here)
        tuning_layout.addWidget(cleaning_group_box)

        # --- Progress, Status Label, and Estimated Time ---
        progress_status_layout = QVBoxLayout()
        self.progress_label = QLabel("Overall Progress:")
        progress_status_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        progress_status_layout.addWidget(self.progress_bar)

        status_time_layout = QHBoxLayout()
        self.current_chapter_status_label = QLabel("Idle")
        self.current_chapter_status_label.setAlignment(Qt.AlignLeft)
        self.current_chapter_status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        status_time_layout.addWidget(self.current_chapter_status_label, 1)


        self.estimated_time_label = QLabel("Estimated Time Remaining: N/A")
        self.estimated_time_label.setAlignment(Qt.AlignRight)
        status_time_layout.addWidget(self.estimated_time_label, 1)


        progress_status_layout.addLayout(status_time_layout)

        left_layout.addWidget(tuning_group_box) # Add tuning group
        left_layout.addLayout(progress_status_layout) # Add progress/status below tuning

        # --- Status Text Area ---
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        # We will add this to the splitter later

        # --- Control Buttons ---
        control_layout = QHBoxLayout()
        self.start_button = QPushButton("Start Scraping")
        self.start_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.start_button.setToolTip("Begin the scraping process with the current settings.")
        self.start_button.clicked.connect(self.start_scraping)
        control_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Scraping")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_button.setToolTip("Gracefully stop the scraping process after the current chapter/batch.")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_scraping) # Connect stop button here
        control_layout.addWidget(self.stop_button)

        # --- Clear Inputs Button ---
        self.clear_inputs_button = QPushButton("Clear Inputs")
        self.clear_inputs_button.setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton)) # Using Cancel icon
        self.clear_inputs_button.setToolTip("Reset all input fields to their default values.")
        self.clear_inputs_button.clicked.connect(self.clear_inputs)
        control_layout.addWidget(self.clear_inputs_button)

        # Clear Log Button
        self.clear_log_button = QPushButton("Clear Log")
        self.clear_log_button.setIcon(self.style().standardIcon(QStyle.SP_DialogResetButton))
        self.clear_log_button.setToolTip("Clear the log messages displayed in the text area.")
        self.clear_log_button.clicked.connect(self.status_text.clear)
        control_layout.addWidget(self.clear_log_button)

        control_layout.addStretch(1) # Push main controls left

        # Button to open output directory
        self.open_output_dir_button = QPushButton("Open Output Folder")
        self.open_output_dir_button.setToolTip("Open the selected output directory in your file explorer.")
        self.open_output_dir_button.clicked.connect(self.open_output_directory)
        self.open_output_dir_button.setEnabled(False)
        control_layout.addWidget(self.open_output_dir_button)


        # left_layout.addSpacing(10) # Remove extra spacing
        left_layout.addLayout(control_layout) # Add to left layout

        # --- Add widgets to splitter ---
        main_splitter.addWidget(scroll_area) # Add the scroll area (containing the panel) to the splitter
        main_splitter.addWidget(self.status_text) # Add log area to the right

        # --- Set initial splitter sizes (adjust ratio for vertical split) ---
        main_splitter.setSizes([500, 300]) # Give more height to controls initially
        # --- Styling and Object Names ---

        # --- Apply More Advanced Dark Theme Styling using QSS ---
        try:
            qss = """
                /* Overall Window and Base Widget Styling */
                QMainWindow, QWidget#scrollAreaWidgetContents {
                    background-color: #23272E; /* Dark background */
                    color: #D1D5DB; /* Light gray text */
                    font-family: "Segoe UI", "Helvetica Neue", "Arial", sans-serif;
                    font-size: 10pt;
                }

                /* Ensure ScrollArea background consistency */
                QScrollArea {
                    border: none; /* Remove border from scroll area itself */
                    background-color: #23272E; /* Match main background */
                }
                QScrollArea > QWidget > QWidget { /* Target the viewport */
                     background-color: #23272E;
                }


                /* GroupBox Styling */
                QGroupBox {
                    background-color: #23272E; /* Match main background */
                    border: 1px solid #374151; /* Subtle border */
                    border-radius: 8px;
                    margin-top: 18px;
                    padding: 15px 10px 10px 10px; /* Adjust padding */
                    color: #D1D5DB;
                    font-weight: bold;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    subcontrol-position: top left;
                    padding: 0 5px;
                    left: 10px;
                    color: #9CA3AF; /* Slightly dimmer title */
                    background-color: #23272E; /* Match main background */
                }

                /* Label Styling */
                QLabel {
                    color: #D1D5DB;
                    background-color: transparent; /* Ensure no unwanted background */
                }
                QLabel#extractedUrlLabel, QLabel#currentChapterStatusLabel, QLabel#estimatedTimeLabel {
                    color: #9CA3AF; /* Dimmer color for status/info labels */
                    font-size: 9pt;
                    font-style: italic;
                }

                /* Input Fields (LineEdit, ComboBox, Specific QTextEdit) */
                QLineEdit, QComboBox, QTextEdit#cleaningPatternsEdit {
                    background-color: #2C313A; /* Distinct dark input background */
                    color: #E5E7EB; /* Slightly brighter text for inputs */
                    padding: 7px;
                    border: 1px solid #4B5563; /* Subtle border */
                    border-radius: 6px;
                    selection-background-color: #3B82F6; /* Blue selection */
                    selection-color: white;
                }
                QLineEdit:readOnly {
                    background-color: #374151; /* Darker for read-only */
                    color: #9CA3AF;
                }
                 /* Style for invalid input */
                 QLineEdit[invalid="true"] {
                     border: 1px solid #EF4444; /* Brighter Red border for invalid */
                 }

                 /* ComboBox Dropdown Arrow and List */
                 QComboBox::drop-down {
                     border: none; /* Remove border around arrow */
                     width: 20px;
                 }
                 QComboBox::down-arrow {
                     image: url(:/qt-project.org/styles/commonstyle/images/downarraow-16.png); /* Use a standard arrow if available */
                 }
                 QComboBox QAbstractItemView { /* Style dropdown list */
                     background-color: #2C313A;
                     color: #E5E7EB;
                     border: 1px solid #4B5563;
                     selection-background-color: #3B82F6;
                 }


                /* General Button Styling */
                QPushButton {
                    color: #E5E7EB;
                    background-color: #374151; /* Gray button base */
                    border: none;
                    padding: 8px 16px;
                    border-radius: 6px;
                    font-weight: 500; /* Medium weight */
                    outline: none;
                }
                QPushButton:hover {
                    background-color: #4B5563; /* Lighter gray on hover */
                }
                QPushButton:pressed {
                    background-color: #5a6472; /* Slightly lighter press */
                }
                 QPushButton:disabled {
                    background-color: #303640; /* Darker disabled */
                    color: #6B7280; /* Dimmed text */
                 }

                /* Gradient Buttons (Primary Actions) */
                 QPushButton#startButton, QPushButton#browseButton {
                     background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6A11CB, stop:1 #2575FC); /* Purple-Blue Gradient */
                     color: white;
                     font-weight: bold;
                 }
                 QPushButton#startButton:hover, QPushButton#browseButton:hover {
                     background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5A0FBB, stop:1 #1E66E0); /* Slightly darker gradient */
                 }
                 QPushButton#startButton:pressed, QPushButton#browseButton:pressed {
                     background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4D0DAA, stop:1 #1A5ACF); /* Even darker gradient */
                 }
                 QPushButton#startButton { padding: 10px 20px; } /* Larger padding for Start */


                 /* Stop Button */
                 QPushButton#stopButton {
                    background-color: #DC2626; /* Red */
                    color: white;
                    font-weight: bold;
                    padding: 10px 20px;
                 }
                 QPushButton#stopButton:hover { background-color: #B91C1C; }
                 QPushButton#stopButton:pressed { background-color: #991B1B; }

                 /* Open Folder Button */
                 QPushButton#openOutputFolderButton {
                     background-color: #F59E0B; /* Amber/Orange */
                     color: #1F2937; /* Dark text */
                     font-weight: bold;
                 }
                 QPushButton#openOutputFolderButton:hover { background-color: #D97706; }
                 QPushButton#openOutputFolderButton:pressed { background-color: #B45309; }

                 /* Config Buttons */
                 QPushButton#saveConfigButton, QPushButton#loadConfigButton, QPushButton#deleteConfigButton {
                     background-color: #4B5563; /* Medium Gray */
                     font-weight: 500;
                 }
                 QPushButton#saveConfigButton:hover, QPushButton#loadConfigButton:hover, QPushButton#deleteConfigButton:hover {
                     background-color: #5a6472;
                 }
                 QPushButton#saveConfigButton:pressed, QPushButton#loadConfigButton:pressed, QPushButton#deleteConfigButton:pressed {
                     background-color: #6B7280;
                 }
                 QPushButton#deleteConfigButton { background-color: #7f1d1d; color: #fecaca; } /* Dark red delete */
                 QPushButton#deleteConfigButton:hover { background-color: #991b1b; }
                 QPushButton#deleteConfigButton:pressed { background-color: #b91c1c; }

                 /* Clear/Test Buttons */
                 QPushButton#clearLogButton, QPushButton#clearInputsButton, QPushButton#testUrlButton {
                    background-color: #374151; /* Darker gray */
                 }
                 QPushButton#clearLogButton:hover, QPushButton#clearInputsButton:hover, QPushButton#testUrlButton:hover {
                    background-color: #4B5563;
                 }
                 QPushButton#clearLogButton:pressed, QPushButton#clearInputsButton:pressed, QPushButton#testUrlButton:pressed {
                    background-color: #5a6472;
                 }


                /* ProgressBar Styling */
                QProgressBar {
                    border: 1px solid #4B5563;
                    border-radius: 6px;
                    background-color: #2C313A; /* Match input background */
                    text-align: center;
                    height: 25px;
                    color: #E5E7EB; /* Match input text */
                }
                 QProgressBar::chunk {
                     background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6A11CB, stop:1 #2575FC); /* Purple-Blue Gradient */
                     border-radius: 5px; /* Slightly less than bar */
                     margin: 1px; /* Add margin to chunk */
                 }

                /* QTextEdit Styling (Log Area) */
                QTextEdit {
                    background-color: #1E2229; /* Slightly darker than main bg */
                    color: #9CA3AF; /* Default log text color */
                    border: 1px solid #374151;
                    border-radius: 6px;
                    padding: 5px;
                    font-family: "Consolas", "Courier New", monospace;
                    font-size: 9pt;
                }

                /* MessageBox Styling */
                QMessageBox {
                    background-color: #23272E; /* Match main window */
                }
                QMessageBox QLabel { /* Style text inside message box */
                    color: #D1D5DB;
                }
                QMessageBox QPushButton { /* Style buttons inside message box */
                    background-color: #374151;
                    color: #E5E7EB;
                    border: none;
                    padding: 6px 12px;
                    border-radius: 4px;
                    min-width: 70px;
                }
                 QMessageBox QPushButton:hover { background-color: #4B5563; }
                 QMessageBox QPushButton:pressed { background-color: #5a6472; }

            """
            self.setStyleSheet(qss)
            self.log_message("Applied refined dark theme styling.", INFO)

        except Exception as e:
            self.log_message(f"Error applying custom styling: {e}", ERROR)


        # --- Set object names for specific styling and easy access ---
        self.browse_button.setObjectName("browseButton")
        self.start_button.setObjectName("startButton")
        self.stop_button.setObjectName("stopButton")
        self.open_output_dir_button.setObjectName("openOutputFolderButton")
        self.clear_log_button.setObjectName("clearLogButton")
        self.clear_inputs_button.setObjectName("clearInputsButton") # Object name
        self.test_url_button.setObjectName("testUrlButton") # Object name
        self.current_chapter_status_label.setObjectName("currentChapterStatusLabel")
        self.estimated_time_label.setObjectName("estimatedTimeLabel")
        self.extracted_url_label.setObjectName("extractedUrlLabel") # Object name
        self.save_config_button.setObjectName("saveConfigButton")
        self.load_config_button.setObjectName("loadConfigButton")
        self.delete_config_button.setObjectName("deleteConfigButton")
        self.cleaning_patterns_edit.setObjectName("cleaningPatternsEdit") # Object name

        self.populate_profiles_combo() # Populate dropdown on startup
        # --- Load default settings (Call this LAST in __init__) ---
        self.load_settings()
        # --- Perform initial validation after loading settings ---
        self.validate_all_inputs()


    @Slot()
    def browse_output_directory(self):
        """Opens a dialog to choose the output directory."""
        current_dir = self.output_dir_entry.text().strip()
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory", current_dir)
        if directory:
            self.output_dir_entry.setText(directory)
            # Settings are saved on close or explicitly saved via config profiles

    # --- Input Validation Slots ---
    @Slot()
    def validate_url_input(self):
        """Validates the URL input and updates the extracted URL label."""
        url = self.url_entry.text().strip()
        is_valid = False
        extracted_pattern = "(Invalid or empty URL)"
        if url:
            match = re.search(r"(.+/chapter-)\d+", url) # Allow trailing chars after number
            if match:
                extracted_pattern = match.group(1)
                is_valid = True
            else:
                extracted_pattern = "(Pattern not found - must end with '/chapter-NUMBER')"

        self.url_entry.setProperty("invalid", not is_valid)
        self.url_entry.setStyle(self.style()) # Force style refresh
        self.extracted_url_label.setText(f"Extracted Base URL: {extracted_pattern}")
        return is_valid

    @Slot()
    def test_url_pattern(self):
        """Tests the URL pattern extraction and shows a message."""
        if self.validate_url_input():
            QMessageBox.information(self, "URL Test", f"Base URL pattern extracted successfully:\n{self.extracted_url_label.text().replace('Extracted Base URL: ','')}")
        else:
            QMessageBox.warning(self, "URL Test", f"Could not extract base URL pattern.\nReason: {self.extracted_url_label.text().replace('Extracted Base URL: ','')}")

    @Slot()
    def validate_numeric_input(self, widget: QLineEdit, min_val=None, max_val=None):
        """Validates a numeric QLineEdit based on its validator and optional min/max."""
        validator = widget.validator()
        text = widget.text()
        state, _, _ = validator.validate(text, 0)
        is_valid = False

        if state == QIntValidator.Acceptable or state == QDoubleValidator.Acceptable:
            try:
                value = float(text) if isinstance(validator, QDoubleValidator) else int(text)
                valid_min = min_val is None or value >= min_val
                valid_max = max_val is None or value <= max_val
                is_valid = valid_min and valid_max
            except ValueError:
                is_valid = False # Should not happen if Acceptable, but safety check
        elif state == QIntValidator.Intermediate or state == QDoubleValidator.Intermediate:
            # Allow intermediate state (e.g., typing '-') unless it's empty
            is_valid = bool(text) and text != '-' and text != '.' and text != '-.' # Consider empty/partial invalid for starting scrape
        else: # Invalid state
            is_valid = False

        widget.setProperty("invalid", not is_valid)
        widget.setStyle(self.style()) # Force style refresh
        return is_valid

    def validate_all_inputs(self):
        """Runs all validation checks and returns True if all are valid."""
        all_valid = True
        if not self.validate_url_input(): all_valid = False

        # Validate numeric fields
        for widget in self.numeric_input_widgets:
            # Retrieve min_val from the validator if possible
            min_val = None
            validator = widget.validator()
            if isinstance(validator, QIntValidator):
                min_val = validator.bottom()
            elif isinstance(validator, QDoubleValidator):
                 min_val = validator.bottom()
            # Call validation
            if not self.validate_numeric_input(widget, min_val=min_val): all_valid = False

        # Specific check: End chapter >= Start chapter
        try:
            start_ch = int(self.start_chapter_entry.text())
            end_ch = int(self.end_chapter_entry.text())
            if end_ch < start_ch:
                self.end_chapter_entry.setProperty("invalid", True)
                self.end_chapter_entry.setStyle(self.style())
                self.log_message("Validation Error: End Chapter must be >= Start Chapter.", WARNING)
                all_valid = False
            # If individually valid but end < start, mark end as invalid
            elif self.end_chapter_entry.property("invalid") == False:
                 pass # Keep valid style if >= start
        except ValueError:
            pass # Individual validation will handle non-numeric

        # Check non-empty fields
        if not self.filename_entry.text().strip():
            self.filename_entry.setProperty("invalid", True); all_valid = False
        else:
            self.filename_entry.setProperty("invalid", False)
        self.filename_entry.setStyle(self.style())

        if not self.output_dir_entry.text().strip():
            # Output dir is read-only, maybe don't mark red, just check in start_scraping
            # self.output_dir_entry.setProperty("invalid", True); all_valid = False
            pass
        # else:
        #     self.output_dir_entry.setProperty("invalid", False)
        # self.output_dir_entry.setStyle(self.style())

        return all_valid
    # --- End Input Validation Slots ---

    @Slot()
    def start_scraping(self):
        """Initiates the scraping process based on UI input."""
        if self.worker_thread is not None and self.worker_thread.isRunning():
            QMessageBox.information(self, "Info", "Scraping is already running.")
            return

        # --- Run validation checks first ---
        if not self.validate_all_inputs():
            QMessageBox.warning(self, "Input Error", "Please fix the errors highlighted in red before starting.")
            return

        # --- Get validated values ---
        sample_url = self.url_entry.text().strip()
        match = re.search(r"(.+/chapter-)\d+", sample_url)
        # This check should be redundant due to validate_all_inputs, but keep as safety
        if not match: QMessageBox.warning(self, "Input Error", "Could not parse base URL pattern."); return
        base_url_pattern = match.group(1)

        try:
            start_chapter = int(self.start_chapter_entry.text().strip())
            end_chapter = int(self.end_chapter_entry.text().strip())
            batch_size = int(self.batch_size_entry.text().strip())
            max_retries = int(self.max_retries_entry.text().strip())
            delay_between_attempts = float(self.delay_entry.text().strip())
        except ValueError as e:
            # Should also be redundant, but safety check
            QMessageBox.critical(self, "Internal Error", f"Could not convert validated input to number: {e}")
            return

        base_filename = self.filename_entry.text().strip()
        output_directory = self.output_dir_entry.text().strip()

        # --- Get Cleaning Patterns ---
        cleaning_patterns_text = self.cleaning_patterns_edit.toPlainText().strip()
        cleaning_patterns = set(line.strip() for line in cleaning_patterns_text.split('\n') if line.strip()) # Use a set for efficient lookup

        # --- Final checks that validation doesn't cover ---
        if end_chapter < start_chapter: QMessageBox.warning(self, "Input Error", "End Chapter Number must be greater than or equal to Start Chapter Number."); return
        if not base_filename: QMessageBox.warning(self, "Input Error", "Please enter an Output File Prefix."); return
        if not output_directory: QMessageBox.warning(self, "Input Error", "Please select an output directory."); return

        if not os.path.isdir(output_directory):
             try: os.makedirs(output_directory); self.log_message(f"Created output directory: {output_directory}", INFO);
             except Exception as e: QMessageBox.critical(self, "Directory Error", f"Could not create output directory: {e}"); return

        driver_found, driver_location = check_chromedriver()
        if not driver_found:
             QMessageBox.critical(self, "Error", f"Chromedriver executable ('{driver_location}') not found.\nPlease download it from the official site and place it in the script folder or your PATH.")
             return

        self.status_text.clear()
        self.log_message("Validation successful. Starting scraping thread...", INFO)

        total_chapters = end_chapter - start_chapter + 1
        self.progress_bar.setMaximum(total_chapters)
        self.progress_bar.setValue(0)
        self.current_chapter_status_label.setText("Initializing...")
        self.estimated_time_label.setText("Estimated Time Remaining: Calculating...")


        # Disable input fields, config controls and enable stop button
        self.set_input_enabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.open_output_dir_button.setEnabled(False)
        self.clear_log_button.setEnabled(False)
        self.clear_inputs_button.setEnabled(False) # Disable clear inputs
        self.test_url_button.setEnabled(False) # Disable test url
        self.set_config_controls_enabled(False)


        self.worker = ScrapingWorker(base_url_pattern, start_chapter, end_chapter, batch_size, base_filename, output_directory, max_retries, delay_between_attempts, cleaning_patterns)
        self.worker_thread = QThread()

        self.worker.moveToThread(self.worker_thread)

        # Connect signals/slots
        self.worker.log_message.connect(self._log_message_to_gui_and_file)
        self.worker.progress_updated.connect(self.progress_bar.setValue)
        self.worker.chapter_scraped.connect(self.handle_chapter_scraped)
        self.worker.saving_error.connect(self.handle_saving_error)
        self.worker.critical_error.connect(self.handle_critical_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.on_scraping_finished)
        self.worker.current_chapter_status.connect(self.current_chapter_status_label.setText) # Connect status label update
        self.worker.scrape_summary.connect(self.display_scrape_summary)
        self.worker.estimated_time_updated.connect(self.estimated_time_label.setText)
        self.worker_thread.started.connect(self.worker.run)
        # Stop button connected in __init__ now

        self.worker_thread.start()

    @Slot(str, int)
    def _log_message_to_gui_and_file(self, message, severity):
        timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        formatted_message = f"{timestamp} {message}\n"

        # Append to QTextEdit with color formatting
        cursor = self.status_text.textCursor()
        cursor.movePosition(QTextCursor.End)

        format = QTextCharFormat()
        color = self.log_colors.get(severity, self.log_colors[INFO])
        format.setForeground(color)

        cursor.insertText(formatted_message, format)
        self.status_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.status_text.ensureCursorVisible()


    @Slot(str)
    # Corrected method signature to accept optional severity
    def log_message(self, message, severity=INFO):
         self._log_message_to_gui_and_file(message, severity)


    @Slot(str, str, int)
    def handle_chapter_scraped(self, title, content, chapter_num):
        pass # Not currently used for GUI updates

    @Slot(str)
    def handle_saving_error(self, error_message):
        self.log_message(f"Saving Error: {error_message}", ERROR)

    @Slot(str)
    def handle_critical_error(self, error_message):
        self.log_message(f"CRITICAL ERROR: {error_message}", CRITICAL)
        QMessageBox.critical(self, "Critical Error", error_message)
        # Consider stopping the process more forcefully here if needed
        self.stop_scraping()


    @Slot(int, list)
    def display_scrape_summary(self, successful_count, failed_chapters):
        """Displays the scraping summary in the log."""
        self.log_message("\n--- Scraping Summary ---", INFO)
        self.log_message(f"Successfully scraped {successful_count} chapters.", INFO)

        if failed_chapters:
            self.log_message(f"Failed to scrape the following {len(failed_chapters)} chapters:", WARNING)
            chunk_size = 10
            for i in range(0, len(failed_chapters), chunk_size):
                 chunk = failed_chapters[i:i+chunk_size]
                 self.log_message(f"  {', '.join(map(str, chunk))}", WARNING)
            # Mention the summary JSON file
            base_filename = self.filename_entry.text().strip() # Get base filename from GUI
            safe_summary_filename = re.sub(r'[\\/:*?"<>|]', '_', f"{base_filename}_summary.json")
            self.log_message(f"Detailed summary saved to: {safe_summary_filename} (in summary directory)", INFO)
        else:
            self.log_message("All chapters scraped successfully.", INFO)


    @Slot()
    def stop_scraping(self):
        """Requests the worker thread to stop and updates the GUI."""
        if self.worker is not None and self.worker_thread is not None and self.worker_thread.isRunning():
             self.log_message("Stop button clicked. Requesting worker stop...", WARNING)
             # Use invokeMethod to ensure stop() is called in the worker's thread context if needed,
             # but calling directly should be fine if stop() is thread-safe (which it is, just sets a flag).
             self.worker.stop()
             self.stop_button.setEnabled(False) # Disable stop button immediately
             self.current_chapter_status_label.setText("Stopping...")
        else:
             self.log_message("Stop clicked, but worker is not running.", INFO)
             self.stop_button.setEnabled(False)

    def write_batch_files(self, successful_content, start_chapter, end_chapter, batch_size, output_directory):
        """Writes the final batch files from the collected content."""
        if not successful_content:
            self.log_message("No successful content collected, skipping final file writing.", WARNING)
            return

        self.log_message("\n--- Writing final batch files in correct order ---", INFO)
        total_chapters_to_write = end_chapter - start_chapter + 1

        for batch_start in range(start_chapter, end_chapter + 1, batch_size):
            batch_end = min(batch_start + batch_size - 1, end_chapter)
            batch_filename = f"{batch_start}-{batch_end}.txt"
            safe_filename = re.sub(r'[\\/:*?"<>|]', '_', batch_filename)
            filepath = os.path.join(output_directory, safe_filename)

            chapters_in_this_batch = []
            for chapter_num in range(batch_start, batch_end + 1):
                if chapter_num in successful_content:
                    chapters_in_this_batch.append(successful_content[chapter_num])
                # else: # Optionally add placeholders for missing chapters in the final file
                #     chapters_in_this_batch.append((f"Chapter {chapter_num} - Title Not Found", "Content Not Found"))

            if chapters_in_this_batch:
                try:
                    with open(filepath, "w", encoding="utf-8") as f:
                        for i, (title, content) in enumerate(chapters_in_this_batch):
                            f.write(f"{title}\n\n{content}\n\n")
                            # Add extra newline between chapters unless it's the last one
                            # if i < len(chapters_in_this_batch) - 1:
                            #     f.write("\n") # Already have \n\n at end of content
                    self.log_message(f"  Successfully wrote batch file: {filepath}", INFO)
                except Exception as e:
                    self.log_message(f"  Error writing final batch file {filepath}: {e}", ERROR)
                    self.saving_error.emit(f"Could not write final batch file {filepath}. Error: {e}")
            else:
                 self.log_message(f"  No successful content for batch {batch_start}-{batch_end}, skipping file: {filepath}", WARNING)


    @Slot()
    def on_scraping_finished(self):
        """Slot to handle GUI updates when the scraping thread finishes."""
        self.log_message("Worker thread finished signal received. Updating GUI...", INFO)
        self.set_input_enabled(True)
        self.start_button.setEnabled(True)

        # --- Write final files ---
        if self.worker and hasattr(self.worker, 'successful_content') and self.worker.successful_content:
            try:
                # Get necessary parameters (ensure they are valid numbers)
                start_ch = int(self.start_chapter_entry.text())
                end_ch = int(self.end_chapter_entry.text())
                batch_s = int(self.batch_size_entry.text())
                output_dir = self.output_dir_entry.text()
                self.write_batch_files(self.worker.successful_content, start_ch, end_ch, batch_s, output_dir)
            except ValueError:
                self.log_message("Error: Could not parse chapter range/batch size for final file writing.", ERROR)
            except Exception as e:
                 self.log_message(f"Error during final file writing process: {e}", ERROR)
        self.stop_button.setEnabled(False) # Ensure stop is disabled
        if self.output_dir_entry.text().strip():
             self.open_output_dir_button.setEnabled(True)
        self.clear_log_button.setEnabled(True)
        self.clear_inputs_button.setEnabled(True) # Enable clear inputs
        self.test_url_button.setEnabled(True) # Enable test url
        self.current_chapter_status_label.setText("Idle" if not self.status_text.toPlainText().strip().endswith("finished ---\n") else "Finished")
        self.estimated_time_label.setText("Estimated Time Remaining: N/A")
        self.set_config_controls_enabled(True)

        # Clean up worker and thread
        if self.worker:
            self.worker.deleteLater()
        if self.worker_thread:
             self.worker_thread.deleteLater()

        self.worker = None
        self.worker_thread = None


    @Slot(bool)
    def set_input_enabled(self, enabled):
        """Enables or disables primary input fields and browse button."""
        for widget in self.input_widgets:
            widget.setEnabled(enabled)
        self.browse_button.setEnabled(enabled)
        self.advanced_options_group.setEnabled(enabled) # Enable/disable advanced group


    @Slot(bool)
    def set_config_controls_enabled(self, enabled):
        """Enables or disables configuration management controls."""
        self.profile_combo.setEnabled(enabled) # Enable/disable combo box
        self.save_config_button.setEnabled(enabled)
        self.load_config_button.setEnabled(enabled)
        self.delete_config_button.setEnabled(enabled) # Enable/disable delete button
        self.profile_name_entry.setEnabled(enabled) # Also enable/disable profile name entry


    @Slot()
    def open_output_directory(self):
        """Opens the saved output directory in the file explorer."""
        output_dir = self.output_dir_entry.text().strip()
        if os.path.isdir(output_dir):
            try:
                if sys.platform == 'win32':
                    os.startfile(output_dir)
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', output_dir])
                else:
                    subprocess.Popen(['xdg-open', output_dir])
            except Exception as e:
                self.log_message(f"Error opening directory {output_dir}: {e}", ERROR)
                QMessageBox.warning(self, "Error", f"Could not open output directory: {e}")
        else:
            self.log_message(f"Output directory does not exist: {output_dir}", WARNING)
            QMessageBox.warning(self, "Warning", f"Output directory does not exist:\n{output_dir}")

    # --- Config Profile Management ---
    @Slot()
    def save_config_profile(self):
        """Saves the current input settings using the 'Profile Name' field."""
        profile_name = self.profile_name_entry.text().strip()
        if not profile_name:
            QMessageBox.warning(self, "Save Profile Error", "Please enter a 'Profile Name' in the Configuration Profiles section.")
            self.profile_name_entry.setFocus()
            return

        self.settings.beginGroup(f"ConfigProfile_{profile_name}")
        self.settings.setValue('url', self.url_entry.text().strip())
        self.settings.setValue('start_chapter', self.start_chapter_entry.text().strip())
        self.settings.setValue('end_chapter', self.end_chapter_entry.text().strip())
        self.settings.setValue('batch_size', self.batch_size_entry.text().strip())
        self.settings.setValue('max_retries', self.max_retries_entry.text().strip())
        self.settings.setValue('delay_between_attempts', self.delay_entry.text().strip())
        self.settings.setValue('base_filename', self.filename_entry.text().strip())
        self.settings.setValue('output_directory', self.output_dir_entry.text().strip())
        # self.settings.setValue('profile_name', profile_name) # No need to save profile name within its own group
        # self.settings.setValue('advanced_options_checked', self.advanced_options_group.isChecked()) # No longer checkable
        self.settings.setValue('cleaning_patterns', self.cleaning_patterns_edit.toPlainText()) # Save cleaning patterns
        self.settings.endGroup()

        self.settings.sync()
        self.log_message(f"Configuration profile '{profile_name}' saved.", INFO)
        # QMessageBox.information(self, "Config Saved", f"Settings saved as profile '{profile_name}'.")
        self.flash_widget_background(self.profile_combo, QColor("#5cb85c")) # Flash green
        self.populate_profiles_combo() # Refresh dropdown
        # Select the newly saved profile
        index = self.profile_combo.findText(profile_name)
        if index != -1:
            self.profile_combo.setCurrentIndex(index)


    @Slot()
    def load_selected_profile_from_combo(self):
        """Loads the profile selected in the QComboBox."""
        profile_name = self.profile_combo.currentText()
        if profile_name:
            self.load_config_profile(profile_name)


    def load_config_profile(self, profile_name):
        """Loads a named configuration profile into the input fields."""
        if not profile_name:
            # This might happen if called directly with no name
            self.log_message("Load Config Error: No profile name provided.", WARNING)
            return

        group_name = f"ConfigProfile_{profile_name}"
        if group_name in self.settings.childGroups():
            self.settings.beginGroup(group_name)
            self.url_entry.setText(self.settings.value('url', ""))
            self.start_chapter_entry.setText(self.settings.value('start_chapter', ""))
            self.end_chapter_entry.setText(self.settings.value('end_chapter', ""))
            self.batch_size_entry.setText(self.settings.value('batch_size', ""))
            self.max_retries_entry.setText(self.settings.value('max_retries', ""))
            self.delay_entry.setText(self.settings.value('delay_between_attempts', ""))
            self.filename_entry.setText(self.settings.value('base_filename', ""))
            self.output_dir_entry.setText(self.settings.value('output_directory', ""))
            self.profile_name_entry.setText(profile_name) # Set profile name field
            self.cleaning_patterns_edit.setPlainText(self.settings.value('cleaning_patterns', "")) # Load cleaning patterns
            # Load advanced options checkbox state, convert string 'true'/'false' to bool
            # is_checked = self.settings.value('advanced_options_checked', "false").lower() == 'true' # No longer checkable
            # self.advanced_options_group.setChecked(is_checked) # No longer checkable
            self.settings.endGroup()

            self.log_message(f"Configuration profile '{profile_name}' loaded.", INFO)
            self.flash_widget_background(self.profile_combo, QColor("#007ACC")) # Flash blue
            self.validate_all_inputs() # Re-validate after loading
        else:
            self.log_message(f"Configuration profile '{profile_name}' not found in settings.", WARNING)
            QMessageBox.warning(self, "Load Config Error", f"Configuration profile '{profile_name}' not found.")


    @Slot()
    def delete_config_profile(self):
        """Deletes the profile selected in the QComboBox."""
        profile_name = self.profile_combo.currentText()
        if not profile_name:
            QMessageBox.warning(self, "Delete Profile Error", "No profile selected in the dropdown to delete.")
            return

        group_name = f"ConfigProfile_{profile_name}"
        if group_name in self.settings.childGroups():
            reply = QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete the profile '{profile_name}'?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.settings.remove(group_name) # QSettings can remove entire groups
                self.settings.sync()
                self.log_message(f"Configuration profile '{profile_name}' deleted.", INFO)
                QMessageBox.information(self, "Config Deleted", f"Profile '{profile_name}' has been deleted.")
                self.populate_profiles_combo() # Refresh dropdown
                # Clear profile name field if the deleted one was showing
                if self.profile_name_entry.text() == profile_name:
                    self.profile_name_entry.clear()
        else:
            self.log_message(f"Configuration profile '{profile_name}' not found for deletion.", WARNING)
            QMessageBox.warning(self, "Delete Config Error", f"Configuration profile '{profile_name}' not found.")

    def populate_profiles_combo(self):
        """Clears and repopulates the profile selection QComboBox."""
        current_profile = self.profile_combo.currentText() # Remember current selection
        self.profile_combo.clear()
        profile_names = []
        prefix = "ConfigProfile_"
        for group in self.settings.childGroups():
            if group.startswith(prefix):
                profile_name = group[len(prefix):]
                profile_names.append(profile_name)

        if profile_names:
            self.profile_combo.addItems([""] + sorted(profile_names)) # Add a blank option first

        # Try to restore previous selection
        index = self.profile_combo.findText(current_profile)
        if index != -1:
            self.profile_combo.setCurrentIndex(index)
        elif self.profile_combo.count() > 0:
             self.profile_combo.setCurrentIndex(0) # Select blank if previous not found

    # --- End Config Profile Management ---

    # --- Utility Slots ---
    @Slot()
    def clear_inputs(self):
        """Clears all input fields and resets them to default or empty."""
        self.log_message("Clearing input fields...", INFO)
        # Clear fields managed by load_settings by calling it
        self.load_settings(use_defaults=True) # Pass flag to force defaults
        # Clear fields not managed by load_settings
        self.profile_name_entry.clear()
        self.cleaning_patterns_edit.clear() # Clear cleaning patterns
        # Reset combo box selection
        if self.profile_combo.count() > 0:
            self.profile_combo.setCurrentIndex(0)
        # Reset validation states
        self.validate_all_inputs()
        self.log_message("Input fields reset to defaults.", INFO)

    def flash_widget_background(self, widget, color, duration=500):
        """Temporarily changes the background color of a widget."""
        original_style = widget.styleSheet()
        widget.setStyleSheet(f"background-color: {color.name()};")
        QTimer.singleShot(duration, lambda: widget.setStyleSheet(original_style))
    # --- End Utility Slots ---


    def closeEvent(self, event):
        """Handles window closing, saves default settings, and stops scraping if running."""
        self.save_settings() # Save current state as default for next launch

        if self.worker_thread is not None and self.worker_thread.isRunning():
            reply = QMessageBox.question(self, "Quit", "Scraping is in progress. Do you want to stop and quit?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.stop_scraping()
                # Give the thread a moment to finish stopping
                if self.worker_thread.isRunning():
                    self.log_message("Waiting for worker thread to finish...", INFO)
                    self.worker_thread.quit() # Ask event loop to quit
                    if not self.worker_thread.wait(5000): # Wait up to 5 seconds
                         self.log_message("Worker thread did not stop gracefully.", WARNING)
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def save_settings(self):
        """Saves the current input settings as the default profile for next launch."""
        self.settings.beginGroup("DefaultConfig")
        self.settings.setValue('url', self.url_entry.text().strip())
        self.settings.setValue('start_chapter', self.start_chapter_entry.text().strip())
        self.settings.setValue('end_chapter', self.end_chapter_entry.text().strip())
        self.settings.setValue('batch_size', self.batch_size_entry.text().strip())
        self.settings.setValue('max_retries', self.max_retries_entry.text().strip())
        self.settings.setValue('delay_between_attempts', self.delay_entry.text().strip())
        self.settings.setValue('base_filename', self.filename_entry.text().strip())
        self.settings.setValue('output_directory', self.output_dir_entry.text().strip())
        self.settings.setValue('profile_name', self.profile_name_entry.text().strip()) # Save last profile name
        # self.settings.setValue('advanced_options_checked', self.advanced_options_group.isChecked()) # No longer checkable
        self.settings.setValue('cleaning_patterns', self.cleaning_patterns_edit.toPlainText()) # Save cleaning patterns
        # Save window geometry
        self.settings.setValue("geometry", self.saveGeometry()) # Re-enable saving geometry
        self.settings.setValue("splitterSizes", self.centralWidget().saveState()) # Save splitter state
        self.settings.endGroup()
        self.settings.sync()
        # self.log_message("Default settings saved.", INFO) # Maybe too noisy

    def load_settings(self, use_defaults=False):
        """Loads the default configuration profile or hardcoded defaults."""
        # Default values (used if no settings or use_defaults is True)
        default_url = "https://wtr-lab.com/en/serie-4881/game-of-thrones-i-loaded-the-witcher-system/old/chapter-121"
        default_start = "1"
        default_end = "10"
        default_batch = "10"
        default_retries = "5"  # Changed default retries
        default_delay = "4.0"  # Changed default delay
        default_filename = "scraped_chapters"
        default_output = os.path.join(os.path.expanduser("~"), "ScrapedChapters")
        default_profile_name = ""
        default_advanced_checked = False
        default_cleaning_patterns = "" # Default cleaning patterns is empty

        if not use_defaults:
            self.settings.beginGroup("DefaultConfig")
            self.url_entry.setText(self.settings.value('url', default_url))
            self.start_chapter_entry.setText(self.settings.value('start_chapter', default_start))
            self.end_chapter_entry.setText(self.settings.value('end_chapter', default_end))
            self.batch_size_entry.setText(self.settings.value('batch_size', default_batch))
            self.max_retries_entry.setText(self.settings.value('max_retries', default_retries))
            self.delay_entry.setText(self.settings.value('delay_between_attempts', default_delay))
            self.filename_entry.setText(self.settings.value('base_filename', default_filename))
            self.output_dir_entry.setText(self.settings.value('output_directory', default_output))
            self.profile_name_entry.setText(self.settings.value('profile_name', default_profile_name))
            self.cleaning_patterns_edit.setPlainText(self.settings.value('cleaning_patterns', default_cleaning_patterns)) # Load cleaning patterns
            # is_checked = self.settings.value('advanced_options_checked', default_advanced_checked, type=bool) # No longer checkable
            # self.advanced_options_group.setChecked(is_checked) # No longer checkable
            # Restore window geometry and splitter state
            geometry = self.settings.value("geometry") # Re-enable restoring geometry
            if geometry: self.restoreGeometry(geometry) # Re-enable restoring geometry
            splitter_state = self.settings.value("splitterSizes")
            if splitter_state: self.centralWidget().restoreState(splitter_state)
            self.settings.endGroup()
        else:
            # Force load hardcoded defaults
            self.url_entry.setText(default_url)
            self.start_chapter_entry.setText(default_start)
            self.end_chapter_entry.setText(default_end)
            self.batch_size_entry.setText(default_batch)
            self.max_retries_entry.setText(default_retries)
            self.delay_entry.setText(default_delay)
            self.filename_entry.setText(default_filename)
            self.output_dir_entry.setText(default_output)
            self.profile_name_entry.setText(default_profile_name)
            self.cleaning_patterns_edit.setPlainText(default_cleaning_patterns) # Load default cleaning patterns
            # self.advanced_options_group.setChecked(default_advanced_checked) # No longer checkable

        # Ensure output directory exists after loading settings
        output_dir = self.output_dir_entry.text().strip()
        if output_dir and not os.path.isdir(output_dir):
            try:
                os.makedirs(output_dir)
                self.log_message(f"Created default/loaded output directory: {output_dir}", INFO)
            except Exception as e:
                self.log_message(f"Warning: Could not create output directory '{output_dir}': {e}", WARNING)


def check_chromedriver():
    """Checks for chromedriver in common locations."""
    chromedriver_name = "chromedriver"
    if sys.platform.startswith('win'):
        chromedriver_name += ".exe"

    # 1. Check in the same directory as the script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(script_dir, chromedriver_name)
    if os.path.exists(local_path):
        return True, local_path

    # 2. Check if it's in the system PATH
    path_location = shutil.which(chromedriver_name)
    if path_location:
        return True, path_location

    # 3. (Optional Windows Specific) Check common Selenium Manager location
    if sys.platform.startswith('win'):
        try:
            # This path might change, relies on internal Selenium structure
            import selenium
            selenium_dir = os.path.dirname(selenium.__file__)
            # Rough guess based on common structure, might need adjustment
            manager_path_segment = os.path.join('webdriver', 'common', 'windows', chromedriver_name)
            potential_path = os.path.join(selenium_dir, '..', 'selenium', manager_path_segment) # Navigate up and into potential manager dir
            if os.path.exists(potential_path):
                 # This path might be deep and version specific, maybe just return True and let Selenium find it?
                 # For now, let's just indicate it *might* be found by Selenium itself.
                 # return True, potential_path
                 pass # Let Selenium handle it if it's in its manager path
        except ImportError:
            pass # Selenium not installed?

    return False, chromedriver_name # Not found in script dir or PATH

if __name__ == "__main__":
    # Check chromedriver before creating QApplication for potential error dialog
    driver_found, driver_location = check_chromedriver()
    if not driver_found:
         # Create a temporary app just for the message box
         temp_app = QApplication.instance() or QApplication(sys.argv)
         QMessageBox.critical(None, "Error", f"Chromedriver executable ('{driver_location}') not found.\nPlease download the version matching your Chrome browser from the official 'Chrome for Testing' site and place '{driver_location}' in the script's folder or add it to your system's PATH.")
         sys.exit(1)

    # Proceed with main application setup
    app = QApplication.instance() or QApplication(sys.argv) # Use existing instance if available

    QCoreApplication.setOrganizationName("YourCompanyName")
    QCoreApplication.setApplicationName("WTRScraper")

    main_window = MainWindow() # Creates window, loads settings (incl. geometry)

    # --- Set geometry manually to available screen size ---
    screen = QApplication.primaryScreen()
    available_geometry = screen.availableGeometry()
    main_window.setGeometry(available_geometry)
    # --- End set geometry ---

    main_window.show() # Use normal show() after setting geometry
    sys.exit(app.exec())
