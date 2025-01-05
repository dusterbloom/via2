#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://va.mite.gov.it"
DOWNLOAD_FOLDER = "downloads"
DELAY_BETWEEN_REQUESTS = 1.0  # polite delay in seconds

# Ensure the base download folder exists
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

def build_search_url(keyword: str, search_type="o", page: int = 1):
    """
    Build the 'Ricerca' URL:
      GET /it-IT/Ricerca/ViaLibera?Testo=<keyword>&t=<search_type>&pagina=<page>

    search_type can be:
      - 'o' => Ricerca Progetti
      - 'd' => Ricerca Documenti
    """
    search_endpoint = "/it-IT/Ricerca/ViaLibera"
    params = {
        "Testo": keyword,
        "t": search_type,
        "pagina": page
    }
    return f"{BASE_URL}{search_endpoint}?{urllib.parse.urlencode(params)}"

def find_total_pages(soup) -> int:
    """
    Extract the total number of pages from the pagination section.
    Example snippet:
      <li class="etichettaRicerca">Pagina 1 di 8</li>
    """
    pag_ul = soup.find("ul", class_="pagination")
    if not pag_ul:
        return 1

    label_li = pag_ul.find("li", class_="etichettaRicerca")
    if not label_li:
        return 1

    match = re.search(r'Pagina\s+(\d+)\s+di\s+(\d+)', label_li.text)
    if match:
        return int(match.group(2))
    return 1

def collect_search_results(keyword: str, search_type="o"):
    """
    Step 1: Perform the search for either 'progetti' (o) or 'documenti' (d)
            and collect the links to project/document detail pages.

    Handles multiple pages based on the pagination info.
    """
    all_links = []
    page = 1

    while True:
        url = build_search_url(keyword, search_type=search_type, page=page)
        print(f"[INFO] Fetching search results: {url}")
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[ERROR] Failed to fetch {url}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Pattern differs for 'progetti' vs. 'documenti'
        # For 'progetti' (o): typically "/it-IT/Oggetti/Info/"
        # For 'documenti' (d): typically "/it-IT/Oggetti/Documentazione/"
        link_pattern = "/it-IT/Oggetti/Info/" if search_type == "o" else "/it-IT/Oggetti/Documentazione/"

        for a in soup.select(f"a[href*='{link_pattern}']"):
            href = a.get("href", "")
            full_url = urllib.parse.urljoin(BASE_URL, href)
            if full_url not in all_links:
                all_links.append(full_url)

        total_pages = find_total_pages(soup)
        print(f"[INFO] Found page {page}/{total_pages}, total detail links so far: {len(all_links)}")

        if page >= total_pages:
            break

        page += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"[INFO] Final count of detail URLs: {len(all_links)}")
    return all_links

def get_project_id(detail_url: str) -> str:
    """
    Extract the project ID from a detail URL.
    Example: /it-IT/Oggetti/Info/10217 -> returns '10217'.
    If not found, return a generic 'UnknownProject'.
    """
    match = re.search(r'/Info/(\d+)', detail_url)
    if match:
        return match.group(1)
    # If your site structure differs, adjust the regex accordingly.
    return "UnknownProject"

def get_procedura_links(detail_url: str, search_type: str):
    """
    Step 2: From the detail page, gather links to the actual "procedure" or "documentazione" pages.
    """
    print(f"[INFO] Parsing detail page => {detail_url}")
    try:
        resp = requests.get(detail_url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[WARN] Could not retrieve {detail_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    procedura_links = []

    # For 'o' or 'd', you might refine these patterns if needed
    link_pattern = "/it-IT/Oggetti/Documentazione/"

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if link_pattern in href:
            full_url = urllib.parse.urljoin(BASE_URL, href)
            if full_url not in procedura_links:
                procedura_links.append(full_url)

    print(f"[INFO] Found {len(procedura_links)} procedure links in {detail_url}.")
    return procedura_links

def get_document_links(procedura_url: str):
    """
    Step 3: Inside a specific procedure page, find the final "Scarica documento" links
    and the 'Nome file' from the table with class 'Documentazione'.

    Returns a list of (download_url, original_filename).
    """
    print(f"[INFO] Parsing procedure page => {procedura_url}")
    try:
        resp = requests.get(procedura_url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[WARN] Could not retrieve {procedura_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="Documentazione")
    doc_links = []

    if not table:
        print(f"[WARN] No 'Documentazione' table found in {procedura_url}.")
        return doc_links

    rows = table.find_all("tr")[1:]  # skip header row
    for row in rows:
        cols = row.find_all("td")
        # Expecting at least 9 columns based on your example
        if len(cols) < 9:
            continue

        nome_file = cols[1].get_text(strip=True)  # second column
        download_td = cols[8]  # ninth column
        download_a = download_td.find("a", href=True, title="Scarica il documento")
        if download_a:
            href = download_a["href"]
            download_url = urllib.parse.urljoin(BASE_URL, href)
            doc_links.append((download_url, nome_file))

    print(f"[INFO] Found {len(doc_links)} document links in {procedura_url}.")
    return doc_links

def download_file(url: str, nome_file: str, save_path: str):
    """
    Step 4: Download the file from the given URL, saving it under 'nome_file' in 'save_path'.
    """
    try:
        # Sanitize filename
        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", nome_file)
        local_path = os.path.join(save_path, safe_filename)

        if os.path.exists(local_path):
            print(f"[INFO] File '{safe_filename}' already exists. Skipping.")
            return

        print(f"[INFO] Downloading: {url}")
        with requests.get(url, stream=True, timeout=20) as r:
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

        print(f"[OK] Saved => {local_path}")

    except Exception as e:
        print(f"[ERROR] Failed to download {url}: {e}")

def main():
    # Prompt user for keyword
    keyword = input("Insert the keyword to search: ").strip()
    if not keyword:
        print("[ERROR] No keyword entered. Exiting.")
        return

    # Prompt user for search type
    print("\nChoose search type:")
    print("1) Progetti (o)")
    print("2) Documenti (d)")
    choice = input("Enter '1' or '2': ").strip()
    if choice == '1':
        search_type = 'o'
        search_type_full = 'Progetti'
    elif choice == '2':
        search_type = 'd'
        search_type_full = 'Documenti'
    else:
        print("[ERROR] Invalid choice. Exiting.")
        return

    # Create the base folder for this search
    safe_keyword = re.sub(r'[\\/*?:"<>|]', "_", keyword)
    base_save_dir = os.path.join(DOWNLOAD_FOLDER, safe_keyword, search_type_full)
    os.makedirs(base_save_dir, exist_ok=True)

    # Step 1: Gather detail pages for this search
    detail_urls = collect_search_results(keyword, search_type=search_type)

    # Step 2: For each detail link, determine project ID, get procedure links, and download docs
    for detail_url in detail_urls:
        project_id = get_project_id(detail_url)
        project_folder = os.path.join(base_save_dir, project_id)
        os.makedirs(project_folder, exist_ok=True)

        procedure_urls = get_procedura_links(detail_url, search_type=search_type)
        for proc_url in procedure_urls:
            doc_links = get_document_links(proc_url)
            for durl, nome_file in doc_links:
                download_file(durl, nome_file, project_folder)
                time.sleep(DELAY_BETWEEN_REQUESTS)

        time.sleep(DELAY_BETWEEN_REQUESTS)

    print("[INFO] Scraping completed successfully.")

if __name__ == "__main__":
    main()
