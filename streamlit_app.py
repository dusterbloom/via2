import streamlit as st
import os
import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup

# -------------------
# Constants
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

@st.cache_data  # caches results for the same keyword & search_type
def collect_search_results(keyword: str, search_type="o"):
    """ Gather all detail page URLs across paginated search results. """
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
    """ Extract a numeric ID from URLs like /Info/1234 or /Documentazione/5678. """
    match = re.search(r'(?:Info|Documentazione)/(\d+)', detail_url)
    if match:
        return match.group(1)
    return "UnknownProject"

def get_procedura_links(detail_url: str):
    """ From a detail page, gather any /it-IT/Oggetti/Documentazione/... procedure links. """
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

def get_document_links(procedura_url: str):
    """ 
    Inside a procedure page, find table(class="Documentazione") rows 
    and extract (download_url, nome_file).
    """
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
    rows = table.find_all("tr")[1:]  # skip header row
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 9:
            continue

        nome_file = cols[1].get_text(strip=True)  # second column
        download_td = cols[8]                     # ninth column
        download_a = download_td.find("a", href=True, title="Scarica il documento")
        if not download_a:
            continue

        href = download_a["href"]
        download_url = urllib.parse.urljoin(BASE_URL, href)
        doc_links.append((download_url, nome_file))

    return doc_links

def download_file(url: str, nome_file: str, save_path: str):
    """ 
    Download the file from `url`, save it as `nome_file` in `save_path`. 
    Filenames are sanitized.
    """
    safe_filename = re.sub(r'[\\/*?:"<>|]', "_", nome_file)
    local_path = os.path.join(save_path, safe_filename)

    if os.path.exists(local_path):
        st.info(f"File '{safe_filename}' already exists. Skipping.")
        return

    try:
        st.write(f"Downloading: {url}")
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
# Streamlit main function
# -------------------------
def main():
    st.title("Scraping App for va.mite.gov.it")
    st.write("Enter a keyword, select Progetti or Documenti, then press **Search**.")
    
    # Inputs
    keyword = st.text_input("Keyword to search", "")
    search_type_map = {"Progetti (o)": "o", "Documenti (d)": "d"}
    search_type_choice = st.selectbox(
        "Choose search type:",
        list(search_type_map.keys()),
        index=0
    )
    search_type = search_type_map[search_type_choice]

    # State to hold results
    if "detail_urls" not in st.session_state:
        st.session_state.detail_urls = []
    if "results_info" not in st.session_state:
        st.session_state.results_info = []  # Will store (detail_url, procedure_url, doc_links)

    # Search button
    if st.button("Search"):
        if not keyword.strip():
            st.error("Please provide a keyword before searching.")
            return

        # Clear previous results
        st.session_state.detail_urls.clear()
        st.session_state.results_info.clear()

        # Set up folder
        safe_keyword = re.sub(r'[\\/*?:"<>|]', "_", keyword)
        search_type_full = "Progetti" if search_type == "o" else "Documenti"
        base_save_dir = os.path.join(DOWNLOAD_FOLDER, safe_keyword, search_type_full)
        os.makedirs(base_save_dir, exist_ok=True)

        # Step 1: Collect detail URLs
        with st.spinner("Collecting search results..."):
            detail_urls = collect_search_results(keyword, search_type=search_type)
        st.success(f"Found {len(detail_urls)} detail URL(s).")

        # Save for next step
        st.session_state.detail_urls = detail_urls

        # Step 2: Parse each detail URL for procedure links & doc links (but do not download yet)
        for detail_url in detail_urls:
            project_id = get_project_id(detail_url)
            project_folder = os.path.join(base_save_dir, project_id)
            os.makedirs(project_folder, exist_ok=True)

            procedure_urls = get_procedura_links(detail_url)
            for proc_url in procedure_urls:
                doc_links = get_document_links(proc_url)
                # Store these results so we can optionally download them
                st.session_state.results_info.append(
                    (detail_url, proc_url, doc_links, project_folder)
                )

        st.success("Parsing complete. Expand the results below or press 'Download All' to proceed.")

    # Show the results of the search
    if st.session_state.detail_urls:
        st.write("---")
        st.write("## Search Results Detail")
        for (detail_url, proc_url, doc_links, folder) in st.session_state.results_info:
            st.write(f"**Detail URL**: {detail_url}")
            st.write(f"&emsp;**Procedure URL**: {proc_url}")
            st.write(f"&emsp;Found **{len(doc_links)}** document(s).")
            if doc_links:
                with st.expander(f"Documents in {proc_url}"):
                    for (durl, nome_file) in doc_links:
                        st.write(f"- **File**: {nome_file}, **Link**: {durl}")

    # Button to download all documents
    if st.session_state.detail_urls:
        if st.button("Download All"):
            st.write("**Starting bulk download...** This may take a while.")
            for (detail_url, proc_url, doc_links, project_folder) in st.session_state.results_info:
                for (durl, nome_file) in doc_links:
                    download_file(durl, nome_file, project_folder)
                    time.sleep(DELAY_BETWEEN_REQUESTS)
            st.success("All files have been downloaded.")

if __name__ == "__main__":
    main()
