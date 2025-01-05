import streamlit as st
import os
import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup

# -------------------
# Original constants
# -------------------
BASE_URL = "https://va.mite.gov.it"
DOWNLOAD_FOLDER = "downloads"
DELAY_BETWEEN_REQUESTS = 1.0  # polite delay in seconds

# Ensure the base download folder exists
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# -------------------
# Utility functions
# -------------------
def build_search_url(keyword: str, search_type="o", page: int = 1):
    search_endpoint = "/it-IT/Ricerca/ViaLibera"
    params = {
        "Testo": keyword,
        "t": search_type,
        "pagina": page
    }
    return f"{BASE_URL}{search_endpoint}?{urllib.parse.urlencode(params)}"

def find_total_pages(soup) -> int:
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

# ---------------------------------------
# Step 1: Collect search results (paged)
# ---------------------------------------
@st.cache_data  # Cache so repeated searches with the same args are faster
def collect_search_results(keyword: str, search_type="o"):
    all_links = []
    page = 1

    while True:
        url = build_search_url(keyword, search_type=search_type, page=page)
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            st.warning(f"Failed to fetch {url}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        if search_type == 'o':
            link_pattern = "/it-IT/Oggetti/Info/"
        else:
            link_pattern = "/it-IT/Oggetti/Documentazione/"

        # parse links
        for a in soup.select(f"a[href*='{link_pattern}']"):
            href = a.get("href", "")
            full_url = urllib.parse.urljoin(BASE_URL, href)
            if full_url not in all_links:
                all_links.append(full_url)

        total_pages = find_total_pages(soup)

        if page >= total_pages:
            break

        page += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

    return all_links

def get_project_id(detail_url: str) -> str:
    match = re.search(r'(?:Info|Documentazione)/(\d+)', detail_url)
    if match:
        return match.group(1)
    return "UnknownProject"

# ---------------------------------------------------------
# Step 2: From a detail page, get the links to procedures
# ---------------------------------------------------------
def get_procedura_links(detail_url: str, search_type: str):
    try:
        resp = requests.get(detail_url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        st.warning(f"Could not retrieve {detail_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    procedura_links = []

    link_pattern = "/it-IT/Oggetti/Documentazione/"

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if link_pattern in href:
            full_url = urllib.parse.urljoin(BASE_URL, href)
            if full_url not in procedura_links:
                procedura_links.append(full_url)

    return procedura_links

# ------------------------------------------------
# Step 3: Inside a procedure, find document links
# ------------------------------------------------
def get_document_links(procedura_url: str):
    try:
        resp = requests.get(procedura_url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        st.warning(f"Could not retrieve {procedura_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="Documentazione")
    if not table:
        return []

    doc_links = []
    rows = table.find_all("tr")[1:]  # skip header
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 9:
            continue

        nome_file = cols[1].get_text(strip=True)
        download_td = cols[8]
        download_a = download_td.find("a", href=True, title="Scarica il documento")
        if not download_a:
            continue

        href = download_a["href"]
        download_url = urllib.parse.urljoin(BASE_URL, href)
        doc_links.append((download_url, nome_file))

    return doc_links

# ---------------------------------------------
# Step 4: Optionally download the file locally
# ---------------------------------------------
def download_file(url: str, nome_file: str, save_path: str):
    try:
        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", nome_file)
        local_path = os.path.join(save_path, safe_filename)

        if os.path.exists(local_path):
            st.info(f"File '{safe_filename}' already exists. Skipping.")
            return

        with requests.get(url, stream=True, timeout=20) as r:
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

        st.success(f"Saved => {local_path}")

    except Exception as e:
        st.error(f"Failed to download {url}: {e}")

# -------------------------
# Streamlit main UI
# -------------------------
def main():
    st.title("Scraping App for va.mite.gov.it")
    st.write("Use the form below to search for *Progetti* or *Documenti* and optionally download them.")

    # User input for the keyword
    keyword = st.text_input("Insert the keyword to search", "")

    # Choice for search type
    search_type_map = {"Progetti (o)": "o", "Documenti (d)": "d"}
    search_type_choice = st.selectbox(
        "Choose search type",
        list(search_type_map.keys()),
        index=0
    )
    search_type = search_type_map[search_type_choice]

    # "Search" button
    if st.button("Search"):
        if not keyword:
            st.error("Please provide a keyword before searching.")
            return

        safe_keyword = re.sub(r'[\\/*?:"<>|]', "_", keyword)
        base_save_dir = os.path.join(DOWNLOAD_FOLDER, safe_keyword, "Progetti" if search_type == "o" else "Documenti")
        os.makedirs(base_save_dir, exist_ok=True)

        with st.spinner("Collecting search results..."):
            detail_urls = collect_search_results(keyword, search_type=search_type)
        st.success(f"Found {len(detail_urls)} detail URLs.")

        # Display the detail URLs
        st.write("### Detail URLs:")
        for u in detail_urls:
            st.write(u)

        # Parse each detail URL for procedure links
        st.write("---")
        st.write("### Parsing each detail URL for procedure links & documents...")
        for detail_url in detail_urls:
            project_id = get_project_id(detail_url)
            project_folder = os.path.join(base_save_dir, project_id)
            os.makedirs(project_folder, exist_ok=True)

            procedure_urls = get_procedura_links(detail_url, search_type=search_type)
            st.write(f"**Detail URL:** {detail_url} — Found {len(procedure_urls)} procedure URLs")

            for proc_url in procedure_urls:
                doc_links = get_document_links(proc_url)
                st.write(f"\n*Procedure URL:* {proc_url} — {len(doc_links)} document(s) found.")
                if doc_links:
                    with st.expander(f"Show documents for {proc_url}"):
                        for (durl, nome_file) in doc_links:
                            st.write(f"**File Name**: {nome_file} | **Download Link**: {durl}")
                            # If you want to automatically download each file, call:
                            # download_file(durl, nome_file, project_folder)
                            # time.sleep(DELAY_BETWEEN_REQUESTS)

                time.sleep(DELAY_BETWEEN_REQUESTS)  # to be polite

            time.sleep(DELAY_BETWEEN_REQUESTS)  # polite delay

        st.success("Scraping completed successfully!")

if __name__ == "__main__":
    main()
